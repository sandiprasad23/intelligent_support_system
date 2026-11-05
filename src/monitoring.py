import os
from threading import Event

from prometheus_client import Counter, Histogram, start_http_server


TICKETS_PROCESSED = Counter(
    "tickets_processed_total",
    "Tickets processed",
    ["channel", "outcome"],
)
RESPONSE_LATENCY = Histogram("response_seconds", "End to end response time in seconds")
GUARDRAIL_BLOCKS = Counter(
    "guardrail_blocks_total",
    "Responses blocked",
    ["guardrail"],
)


def start_metrics_server(port=8001):
    port = int(os.getenv("METRICS_PORT", port))
    start_http_server(port)
    print(f"Prometheus metrics available at http://localhost:{port}/metrics")


def main():
    start_metrics_server()
    Event().wait()


if __name__ == "__main__":
    main()
