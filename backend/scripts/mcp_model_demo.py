"""Local MCP contract demo (not a trained production model).

Run from backend: python -m uvicorn scripts.mcp_model_demo:app --port 8091
"""

from fastapi import FastAPI, Request, Response

app = FastAPI()
TOOL = {
    "name": "predict_output", "description": "Contract demo: output = hours * 10",
    "annotations": {"readOnlyHint": True},
    "inputSchema": {"type": "object", "required": ["records"], "additionalProperties": False,
                    "properties": {"records": {"type": "array", "minItems": 1, "maxItems": 200,
                        "items": {"type": "object", "required": ["hours"], "additionalProperties": False,
                                  "properties": {"hours": {"type": "number"}}}}}},
    "outputSchema": {"type": "object", "required": ["predictions"],
                     "properties": {"predictions": {"type": "array", "items": {"type": "number"}}}},
}


@app.post("/mcp")
async def mcp(request: Request):
    payload = await request.json()
    if "id" not in payload:
        return Response(status_code=202)
    method = payload.get("method")
    if method == "initialize":
        result = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "forgesight-demo", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": [TOOL]}
    elif method == "tools/call" and payload.get("params", {}).get("name") == TOOL["name"]:
        try:
            predictions = [row["hours"] * 10 for row in payload["params"]["arguments"]["records"]]
            result = {"content": [], "structuredContent": {"predictions": predictions}}
        except (KeyError, TypeError):
            result = {"isError": True, "content": [{"type": "text", "text": "invalid input"}]}
    else:
        return {"jsonrpc": "2.0", "id": payload["id"], "error": {"code": -32601, "message": "Unknown method"}}
    return {"jsonrpc": "2.0", "id": payload["id"], "result": result}
