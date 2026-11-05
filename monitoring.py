from threading import Event

from prometheus_client import Counter, Histogram, start_http_server


TICKETS = Counter(
    "tickets_processed_total",
    "Tickets processed",
    ["channel", "outcome"],
)
LATENCY = Histogram("response_seconds", "End to end response time")
GUARDRAIL = Counter(
    "guardrail_blocks_total",
    "Responses blocked",
    ["guardrail"],
)


if __name__ == "__main__":
    start_http_server(8081)
    print("Prometheus metrics available at http://localhost:8081/metrics")
    Event().wait()