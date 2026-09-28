"""fraud-service: near-real-time fraud features from payment events.

Reads the Kafka topic of the governed subject, strips the 5-byte Confluent wire
header, decodes with the registry's latest schema, and selects feature columns.
"""
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql.avro.functions import from_avro
from pyspark.sql.functions import col, expr

from common.registry import latest_schema

SUBJECT = "payments.public.transactions-value"
TOPIC = "payments.public.transactions"


def build_features(spark: SparkSession) -> DataFrame:
    value_schema = latest_schema(SUBJECT)
    raw = (spark.readStream.format("kafka")
           .option("kafka.bootstrap.servers", "localhost:9092")
           .option("subscribe", TOPIC)
           .option("startingOffsets", "latest")
           .load())
    payload = raw.select(expr("substring(value, 6, length(value) - 5)").alias("avro_value"))
    events = payload.select(from_avro(col("avro_value"), value_schema).alias("e"))
    features = events.select("e.transaction_id", "e.merchant_id", "e.customer_id",
                             "e.currency", "e.amount_minor", "e.status", "e.event_ts")
    return features.where(col("status") != "DECLINED")
