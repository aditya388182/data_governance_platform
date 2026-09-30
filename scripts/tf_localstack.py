#!/usr/bin/env python3
import argparse
import json
import os
import sys

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

EP = os.environ.get("LOCALSTACK_ENDPOINT", "http://localhost:4566")
STATE_BUCKET, LOCK_TABLE = "tf-state", "tf-locks"


def client(svc):
    return boto3.client(svc, endpoint_url=EP, region_name="us-east-1", aws_access_key_id="test",
                        aws_secret_access_key="test", config=Config(s3={"addressing_style": "path"},
                                                                     retries={"max_attempts": 3}))


def bootstrap():
    s3, ddb = client("s3"), client("dynamodb")
    try:
        s3.create_bucket(Bucket=STATE_BUCKET)
        print(f"created s3://{STATE_BUCKET}")
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            raise
        print(f"s3://{STATE_BUCKET} already exists")
    s3.put_bucket_versioning(Bucket=STATE_BUCKET, VersioningConfiguration={"Status": "Enabled"})
    try:
        ddb.create_table(TableName=LOCK_TABLE, BillingMode="PAY_PER_REQUEST",
                         AttributeDefinitions=[{"AttributeName": "LockID", "AttributeType": "S"}],
                         KeySchema=[{"AttributeName": "LockID", "KeyType": "HASH"}])
        ddb.get_waiter("table_exists").wait(TableName=LOCK_TABLE)
        print(f"created DynamoDB table {LOCK_TABLE} (hash key LockID)")
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceInUseException":
            raise
        print(f"DynamoDB table {LOCK_TABLE} already exists")
    print(f"backend ready at {EP}: state versioning on, locking on")


def state():
    s3, ddb = client("s3"), client("dynamodb")
    objs = s3.list_objects_v2(Bucket=STATE_BUCKET).get("Contents", [])
    print(f"s3://{STATE_BUCKET}: {len(objs)} state object(s)")
    for o in objs:
        print(f"  {o['Key']:48s} {o['Size']:7d} B  {o['LastModified']:%Y-%m-%d %H:%M:%S}")
    items = ddb.scan(TableName=LOCK_TABLE).get("Items", [])
    held = [i for i in items if "Info" in i]
    print(f"DynamoDB {LOCK_TABLE}: {len(items)} item(s), {len(held)} lock(s) held")
    for i in held:
        info = json.loads(i["Info"]["S"])
        print(f"  LOCK {info.get('ID')}  op={info.get('Operation')}  who={info.get('Who')}  since={info.get('Created')}")


def console_change(workspace, tag):
    key, _, value = tag.partition("=")
    if not key or not value:
        sys.exit("--tag KEY=VALUE")
    bucket = f"p4-{workspace}-governed-lake"
    s3 = client("s3")
    try:
        tags = {t["Key"]: t["Value"] for t in s3.get_bucket_tagging(Bucket=bucket)["TagSet"]}
    except ClientError as e:
        sys.exit(f"cannot read {bucket}: {e.response['Error']['Code']} (apply the {workspace} workspace first)")
    before = tags.get(key)
    tags[key] = value
    s3.put_bucket_tagging(Bucket=bucket, Tagging={"TagSet": [{"Key": k, "Value": v} for k, v in sorted(tags.items())]})
    print(f"OUT-OF-BAND CHANGE (no PR, no plan, no state): s3://{bucket} tag {key}: {before!r} -> {value!r}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["bootstrap", "state", "console-change"])
    ap.add_argument("--workspace", default="dev")
    ap.add_argument("--tag")
    a = ap.parse_args()
    if a.cmd == "bootstrap":
        bootstrap()
    elif a.cmd == "state":
        state()
    else:
        console_change(a.workspace, a.tag or sys.exit("--tag KEY=VALUE"))
