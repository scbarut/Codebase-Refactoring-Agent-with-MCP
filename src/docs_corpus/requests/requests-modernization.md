# Requests Modernization Guide

## Mandatory Timeouts
Every outgoing HTTP request using `requests.get()`, `requests.post()`, etc., should include an explicit `timeout` argument (e.g., `timeout=10` or `timeout=(3.05, 27)`). Without a timeout, requests will block indefinitely if the remote server fails to respond.

```python
# Before:
response = requests.get("https://api.example.com/items")

# After:
response = requests.get("https://api.example.com/items", timeout=10)
```

## JSON Parameter
Instead of manually serializing a payload with `json.dumps()` and setting `Content-Type: application/json` headers, use the native `json` parameter in requests methods:

```python
# Before:
import json
response = requests.post("https://api.example.com/items", data=json.dumps(payload), headers={"Content-Type": "application/json"}, timeout=10)

# After:
response = requests.post("https://api.example.com/items", json=payload, timeout=10)
```
