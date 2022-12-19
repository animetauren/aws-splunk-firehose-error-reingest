"""
This Lambda function is to be added to a Kinesis Firehose Data Stream.
Only For processing data sent to Firehose by Re-ingest process. Note: Records are not converted/transformed.

"""

import base64
import boto3
import os
import json

sns=boto3.client('sns')

def processRecords(records):
    
    for r in records:
        
        data = json.loads(base64.b64decode(r['data']))
        recId = r['recordId']
        return_event = {}

        try:
            return_event['sourcetype'] = data['sourcetype']
            break
        except Exception as e:
            print("Error:" + str(e))
            print(data)
        return_event['source'] = data['source']
        return_event['event'] = data['event']
        if data.get('time')!=None:
            return_event['time'] = data['time']
        if data.get('fields')!=None:
            return_event['fields'] = data['fields']

        data = base64.b64encode(json.dumps(return_event).encode('utf-8')).decode()
        yield {
            'data': data,
            'result': 'Ok',
            'recordId': recId
        }


def putRecordsToFirehoseStream(streamName, records, client, attemptsMade, maxAttempts):
    failedRecords = []
    codes = []
    errMsg = ''
    # if put_record_batch throws for whatever reason, response['xx'] will error out, adding a check for a valid
    # response will prevent this
    response = None
    try:
        response = client.put_record_batch(DeliveryStreamName=streamName, Records=records)
    except Exception as e:
        failedRecords = records
        errMsg = str(e)

    # if there are no failedRecords (put_record_batch succeeded), iterate over the response to gather results
    if not failedRecords and response and response['FailedPutCount'] > 0:
        for idx, res in enumerate(response['RequestResponses']):
            # (if the result does not have a key 'ErrorCode' OR if it does and is empty) => we do not need to re-ingest
            if 'ErrorCode' not in res or not res['ErrorCode']:
                continue
            
            if 'ServiceUnavailableException' in res['ErrorMessage']:
                print("ServiceUnavailableException")
                print("The service (Kinesis Firehose) is unavailable.\n Back off and retry the operation.\n If you continue to see the exception,\n throughput limits for the delivery stream may have been exceeded.")
            
            codes.append(res['ErrorCode'])
            failedRecords.append(records[idx])

        errMsg = 'Individual error codes: ' + ','.join(codes)

    if len(failedRecords) > 0:
        if attemptsMade + 1 < maxAttempts:
            print('Some records failed while calling PutRecordBatch to Firehose stream, retrying. %s' % (errMsg))
            putRecordsToFirehoseStream(streamName, failedRecords, client, attemptsMade + 1, maxAttempts)
        else:
            raise RuntimeError('Could not put records after %s attempts. %s' % (str(maxAttempts), errMsg))


def putRecordsToKinesisStream(streamName, records, client, attemptsMade, maxAttempts):
    failedRecords = []
    codes = []
    errMsg = ''
    # if put_records throws for whatever reason, response['xx'] will error out, adding a check for a valid
    # response will prevent this
    response = None
    try:
        response = client.put_records(StreamName=streamName, Records=records)
    except Exception as e:
        failedRecords = records
        errMsg = str(e)

    # if there are no failedRecords (put_record_batch succeeded), iterate over the response to gather results
    if not failedRecords and response and response['FailedRecordCount'] > 0:
        for idx, res in enumerate(response['Records']):
            # (if the result does not have a key 'ErrorCode' OR if it does and is empty) => we do not need to re-ingest
            if 'ErrorCode' not in res or not res['ErrorCode']:
                continue
            
            if 'ServiceUnavailableException' in res['ErrorMessage']:
                print("ServiceUnavailableException")
                print("The service (Kinesis Stream) is unavailable.\n Back off and retry the operation.\n If you continue to see the exception,\n throughput limits for the delivery stream may have been exceeded.")

            codes.append(res['ErrorCode'])
            failedRecords.append(records[idx])

        errMsg = 'Individual error codes: ' + ','.join(codes)

    if len(failedRecords) > 0:
        if attemptsMade + 1 < maxAttempts:
            print('Some records failed while calling PutRecords to Kinesis stream, retrying. %s' % (errMsg))
            putRecordsToKinesisStream(streamName, failedRecords, client, attemptsMade + 1, maxAttempts)
        else:
            raise RuntimeError('Could not put records after %s attempts. %s' % (str(maxAttempts), errMsg))


def createReingestionRecord(isSas, originalRecord):
    if isSas:
        return {'data': base64.b64decode(originalRecord['data']), 'partitionKey': originalRecord['kinesisRecordMetadata']['partitionKey']}
    else:
        return {'data': base64.b64decode(originalRecord['data'])}

def getReingestionRecord(isSas, reIngestionRecord):
    if isSas:
        return {'Data': reIngestionRecord['data'], 'PartitionKey': reIngestionRecord['partitionKey']}
    else:
        return {'Data': reIngestionRecord['data']}

def postResultToSNS(TopicArn, msg_string):
    try:
        response = sns.publish(
            TopicArn=TopicArn,
            Message=msg_string,
            MessageStructure='string',
            MessageAttributes={
                'string': {
                    'DataType': 'string',
                    'StringValue': 'string',
                    'BinaryValue': b'bytes'
                }
            }
        )
    except Exception as e:
        #Print error message, and send failure notification
        print(e)           
        raise e          

def lambda_handler(event, context):

    # try:
    #     TopicArn=os.environ['TopicArn']
    # except:
    #     print('TopicArn variable is not set!!') 

    isSas = 'sourceKinesisStreamArn' in event
    streamARN = event['sourceKinesisStreamArn'] if isSas else event['deliveryStreamArn']
    region = streamARN.split(':')[3]
    streamName = streamARN.split('/')[1]
    records = list(processRecords(event['records']))
    projectedSize = 0
    dataByRecordId = {rec['recordId']: createReingestionRecord(isSas, rec) for rec in event['records']}
    putRecordBatches = []
    recordsToReingest = []
    totalRecordsToBeReingested = 0

    for idx, rec in enumerate(records):
        if rec['result'] != 'Ok':
            continue
        projectedSize += len(rec['data']) + len(rec['recordId'])
        # 6000000 instead of 6291456 to leave ample headroom for the stuff we didn't account for
        if projectedSize < 6000000:
            totalRecordsToBeReingested += 1
            recordsToReingest.append(
                getReingestionRecord(isSas, dataByRecordId[rec['recordId']])
            )
            records[idx]['result'] = 'Dropped'
            del(records[idx]['data'])

        # split out the record batches into multiple groups, 500 records at max per group
        if len(recordsToReingest) == 500:
            putRecordBatches.append(recordsToReingest)
            recordsToReingest = []

    if len(recordsToReingest) > 0:
        # add the last batch
        putRecordBatches.append(recordsToReingest)

    # iterate and call putRecordBatch for each group
    recordsReingestedSoFar = 0
    if len(putRecordBatches) > 0:
        client = boto3.client('kinesis', region_name=region) if isSas else boto3.client('firehose', region_name=region)
        for recordBatch in putRecordBatches:
            if isSas:
                putRecordsToKinesisStream(streamName, recordBatch, client, attemptsMade=0, maxAttempts=20)
            else:
                putRecordsToFirehoseStream(streamName, recordBatch, client, attemptsMade=0, maxAttempts=20)
                recordsReingestedSoFar += len(recordBatch)
            
            #Send Success Message to SNS    
            # postResultToSNS(TopicArn, "Re-Ingest Sucessfull!")            
            print('Reingested %d/%d records out of %d' % (recordsReingestedSoFar, totalRecordsToBeReingested, len(event['records'])))

    else:
        # postResultToSNS(TopicArn, "Re-Ingest Failed!")
        print('No records to be reingested')

    return {"records": records}
