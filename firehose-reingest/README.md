# Re-Ingestion Pipeline for Failed Firehose outputs to Splunk from S3 via Firehose

This solution is to re-ingest logs stored in AWS S3 that originally failed to be ingested into Splunk via Amazon Kinesis Data Firehose (KDF). In the case when KDF cannot write data to Splunk via the HTTP Event Collector (HEC), the undeliverable logs will be stored in a "splashback" S3 Bucket configured KDF. The logs will be wrapped by additional information relating to the KDF to Splunk failure, and the original message will be encoded. This new log message cannot be ingested natively via the AWS for Splunk Add-On, this requires another solution.  

## Prerequisties

- [AWS CLI Configured](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
- AWS Admin Account with proper permissions
- Create a new [AWS S3 Bucket](https://docs.aws.amazon.com/AmazonS3/latest/userguide/creating-bucket.html) that will temporarily store your data before re-ingestion (e.g. "reingestbucket").
- Create new [Splunk HEC URI](https://docs.splunk.com/Documentation/SplunkCloud/9.0.2208/Data/UsetheHTTPEventCollector#Send_data_to_HTTP_Event_Collector_on_Splunk_Cloud_Platform) for re-ingestion pipeline
- Create new [Splunk HEC Authentication Token](https://docs.splunk.com/Documentation/Splunk/9.0.1/Data/UsetheHTTPEventCollector#Configure_HTTP_Event_Collector_on_Splunk_Cloud_Platform) for re-ingestion pipeline

This solution consists of two functions:

- [S3 Re-ingest Lambda Function](https://github.com/animetauren/aws-splunk-firehose-error-reingest/blob/main/firehose-reingest/lambda_function.py)
- [Kinesis Transformation Lambda Function](https://github.com/animetauren/aws-splunk-firehose-error-reingest/blob/main/firehose-reingest/kinesis_lambda_function.py)

The S3 Re-ingest Lambda Function function allows a re-ingest process to be possible. It is triggered  in S3, and will read and decode the payload, writing the output back into Firehose (same one or a different specific one to re-ingest, e.g. to a different Splunk instance). Care should be taken if re-ingesting back into the same Firehose in case that the reasons for failure to write to Splunk are not related to connectivity (you could flood your Firehose with a continuous error loop).
Also note that the function contains some logic to push out a re-ingested payload into S3 once the re-try has failed a maximum number of times. The messages will return to the original S3 bucket under the **SplashbackRawFailed/** prefix. This will keep sourcetypes from each of the firehoses that push to this re-try function separate.

This capability will prevent a "looping" scenario where the events are constantly re-ingested if the same firehose is used to "re-try", and if the connectivity is not re-established with Splunk.

Note that this is a template example, that is based on re-ingesting from a Kinesis Data Firehose configuration set up using HEC to Splunk. It is assuming that a NEW Kinesis Data Firehose data stream is set up to do the Re-ingest process itself. This is so that it can provide a generic solution for different sources of messages that pass through Firehose. (The format of the json messages that may come in via a different set-up may need some small changes to the construct of the re-ingested json payload.)

Note that the "Splashback" S3 bucket where KDF sends the failed messages also contains objects (with different prefixes) that would not necessarily be suitable to ingest from - for example, if there is a pre-processing function set up (a lambda function for the Firehose), the failiure could be caused there - these events will have a "processing-failed/" prefix. As additional processing would have been done to the payloads of these events, the contents of the "raw" event may not be what you wish to ingest into Splunk. This is why the Event notification for these functions should always include the prefix "splunk-failed/" to ensure that only those with a completed processing are read into Splunk via this "splashback" route.

## Setup Process

1. Create a new S3 Bucket for Re-ingesting Data

2. Create a new AWS Lambda Function [Kinesis Transformation Lambda Function](https://github.com/animetauren/aws-splunk-firehose-error-reingest/blob/main/firehose-reingest/kinesis_lambda_function.py)
    (Author from scratch)  
    Select Python 3.9 as the runtime  
    Select Permissions  
    Create a new role with basic Lambda permissions  
    Click "Create function"  
    Copy the function code from Kinesis Transformation Lambda Function, and replace/paste into your lambda function code.  
    Increase the function timeout to 5 minutes  
    Deploy the function.  

3. Set up a new AWS Kinesis Firehose for Re-ingesting
    Create a new Kinesis Data Firehose Delivery Stream  
    Select "Direct PUT or other sources"  
    Select Destination as Splunk  
    Give the delivery stream a name (e.g. KDF_Splunk_Reingest)  
    Enable Data Transformation under Transfer Records  
    Select the new lambda function (Kinesis Transformation Lambda)  
    Enter the Splunk Cluster Endpoint URL (e.g. ``https://http-inputs-firehose-<your stack hostname goes here>.splunkcloud.com/services/collector/raw``)
    Select Raw endpoint  
    Add existing HEC Authentication Token  
    Change Retry Duration to a high value - suggested 600 seconds or above.  
    Either Create a New S3 bucket OR select an existing bucket for the destination of "Backup S3 bucket"  
    Accept all other defaults for rest of configuration and Create delivery stream  

4. Create a new AWS Lambda Function [S3 Re-ingest Lambda Function](https://github.com/animetauren/aws-splunk-firehose-error-reingest/blob/main/firehose-reingest/lambda_function.py)
    (Author from scratch)  
    Select Python 3.9 as the runtime  
    Select Permissions  
    Create a new role from AWS policy templates  
    Give it a Role Name  
    Select "Amazon S3 object read-only permissions" from the Policy Templates  
    Click "Create function"  
    Copy the function code from S3 Re-ingest Lambda Function, and replace/paste into your lambda function code.  
    Update the permissions: Adding write permissions to Kinesis Firehose  
    On your new function, select the "Permissions" tab, and click on the Execution role Role name (it will open up a new window with IAM Manager)  
    In the Permissions Tab, you will see two attached policies, click "Add inline policy".  
    In the Visual Editor add the following:  
      1. Service - Firehose  
      2. Actions - Write - PutRecordBatch  
      3. Resources - Either enter the ARN for your Firehose OR tick the "Any in this account"  

    Click Review Policy, and Save Changes  
    Increase the Timeout for the function to 5 minutes  

5. Update environment variables for the “S3 Re-ingest Lambda Function
    Add the following environment variables:
      1. **Firehose** - set the value to the name of the firehose that you wish to "reingest" the messages
      2. **Region** - set the value of the AWS region where the firehose is set up
      3. (optional) **Cleanup** - set this value to "True" if you would like your placeholder s3 bucket cleaned. If not set, defaults to "False".

    Deploy the Function

6. Create S3 Event Notification on the S3 Re-ingesting Data Bucket  
    Navigate to your AWS S3 Re-ingesting Data bucket in the AWS S3 console  
    Choose Properties  
    Navigate to the Event Notifications section and choose Create event notification.  
    Give the event notification a name, and ensure you add the prefix "splunk-failed/"*
    Select the "All object create events" check box.  
    Select "Lambda Function" as the Destination, and select the S3 Re-ingest Lambda Function.  
    Save Changes

7. *Conditional* If running Splunk Enterprise:
     Configure HTTP Event Collector (HEC) indexer acknowledgment - [Splunk Docs Instructions](https://docs.splunk.com/Documentation/Splunk/9.0.3/Data/AboutHECIDXAck)

## Notes

In Step 6, if you have added another prefix in your KDF configuration, you will need to add that to this prefix (e.g.  if you added FH as the prefix in KDF config, you will need to add "FHsplunk-failed/").

You are now all set with the solution.

## IAM Roles

Below are example IAM Role definitions used for each of the above Lambda functions. It is your responsbility to review the permissions, and follow best practices (e.g. LPA).

- [S3 Re-Ingest Lambda IAM Role Example](https://github.com/animetauren/aws-splunk-firehose-error-reingest/blob/main/S3ReingestLambdaIAMRole.json)  
- [Kinesis Lambda IAM Role Example](https://github.com/animetauren/aws-splunk-firehose-error-reingest/blob/main/KinesisTransformationLambdaIAMRole.json)  

# Triggering the Re-Ingestion Pipeline

The Re-Ingestion pipeline must is not triggered automatically, this is by design. This is done to force then user to first fix the issue affecting getting data into Splunk. Once the issue has been resolved, then this pipeline can be triggered, starting the re-ingestion process into Splunk.

The reccomended approach to trigger the Re-Ingestion pipeline is to copy the "splashback logs" into a "reingestion bucket". An example of how this can be done is below using AWS CLI.

Commands:
`aws s3 sync <source> <dest> --include "*splunk-failed/YYYY/MM/DD/HH/*"`

Example:
`aws s3 sync s3://example-splashback s3://example-reingest --include "*splunk-failed/2022/01/01/12/*"`

# Current Limitations

The function takes into consideration a "loop" scenario where data could potentially continiously re-ingest if there is no way of connecting back to Splunk HEC. Without this, it could essentially build up significant volumes in re-ingest and max-out the Firehose capacity. Increasing the Retry duration setting on Firehose can minimise this, but this will become an issue if Splunk becomes unavailable for a very long period.
