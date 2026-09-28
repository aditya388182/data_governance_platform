# fraud-service (stand-in) — team: fraud-team
Streams `payments.public.transactions` from Kafka, decodes Confluent-framed Avro with
the registry's latest schema, and builds per-event fraud features.
