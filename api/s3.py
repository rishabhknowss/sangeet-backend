import boto3

# Create an S3 resource
s3_resource = boto3.resource('s3')

# Specify the bucket name
bucket_name = 'sangeet'

# Access the bucket
bucket = s3_resource.Bucket(bucket_name)

# List objects in the bucket
for obj in bucket.objects.all():
    print(obj.key)
