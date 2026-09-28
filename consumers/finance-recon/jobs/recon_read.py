from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

TRANSACTIONS_PATH = "s3a://governance-lake/transactions"


def daily_totals(spark: SparkSession, day: str) -> DataFrame:
    df = spark.read.format("delta").load(TRANSACTIONS_PATH)
    return (df.where(F.to_date(F.from_unixtime(F.col("event_ts") / 1000)) == F.lit(day))
              .groupBy("currency")
              .agg(F.sum("amount_minor").alias("total_minor"), F.count("*").alias("n_txns")))
