"""marketing-etl: weekly spend per customer for campaign segmentation."""
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F


def weekly_spend(spark: SparkSession) -> DataFrame:
    txns = spark.read.format("delta").load("s3a://governance-lake/transactions")
    return (txns.selectExpr("customer_id", "amount_minor", "currency",
                            "date_trunc('week', timestamp_millis(event_ts)) AS week")
                .groupBy("customer_id", "week", "currency")
                .agg(F.sum("amount_minor").alias("spend_minor")))
