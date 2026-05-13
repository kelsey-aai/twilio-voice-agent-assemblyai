"""
Tool definitions and dispatcher for the Twilio voice agent.

Replace the stubs in dispatch_tool with calls to your real systems:
- get_order_status -> your order management system / Shopify / ERP
- transfer_to_human -> your contact center / call routing system
"""

import json

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_order_status",
            "description": (
                "Look up the status of a customer order by order ID. "
                "Use this when the caller asks about a specific order."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description": "The order ID, e.g. AB3792",
                    }
                },
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "transfer_to_human",
            "description": (
                "Transfer the caller to a human agent. Use when the caller "
                "explicitly asks for a person, is upset, or needs help with "
                "something outside order status."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Brief reason for the transfer.",
                    }
                },
                "required": ["reason"],
            },
        },
    },
]


# Demo data — replace with a real lookup in production.
_DEMO_ORDERS = {
    "AB3792": "Shipped. Expected delivery is Thursday, May 14.",
    "CD1204": "Processing. Ships within two business days.",
    "EF5566": "Delivered on May 8. Was left at the front door.",
}


async def dispatch_tool(name: str, arguments_json: str) -> str:
    args = json.loads(arguments_json)
    if name == "get_order_status":
        order_id = args.get("order_id", "").strip().upper()
        result = _DEMO_ORDERS.get(order_id)
        if result:
            return f"Order {order_id}: {result}"
        return f"I couldn't find order {order_id}. Could you double-check the ID?"
    if name == "transfer_to_human":
        reason = args.get("reason", "")
        # In production: hand off to your contact center with full context.
        print(f"[SYSTEM] Transfer requested. Reason: {reason}")
        return "Transferring you now. Please hold for a moment."
    return f"Unknown tool: {name}"
