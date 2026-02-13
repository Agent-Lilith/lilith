## Tool Calling Protocol

To use a tool, place a JSON object in a fenced code block at the **end** of your response:

```json
{"tool": "tool_name", "param1": "value1", "param2": "value2"}
```

**Rules:**
1. **Valid JSON:** The code block must contain a single, valid JSON object.
2. **Last in response:** The JSON code block must be the last thing in your response. No text after the closing ```.
3. **Reasoning before, not after:** You may include brief reasoning or a `<think>` block before the tool call. Do not add explanation after it.
4. **No narration:** Do not narrate what the tool will do (e.g., "Let me search for that..."). Just call it.
5. **One tool per response:** Call exactly one tool per response. Wait for the result before the next call.

{tool_examples}
