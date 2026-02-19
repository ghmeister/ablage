# Migration from Claude (Anthropic) back to OpenAI

This document outlines the migration from Anthropic's Claude 3.5 Sonnet to OpenAI's GPT-4o models for the PDF Renamer Bot.

## Overview

We reverted the AI provider to OpenAI to streamline configuration, reduce latency, and align with our broader platform usage. The bot now uses OpenAI's `gpt-4o-mini` for both filename suggestions and document metadata extraction.

## What Changed

### Dependencies

- **Before**: `anthropic>=0.18.0`
- **After**: `openai>=1.0.0`

### Environment Variables

- **Before**: `ANTHROPIC_API_KEY`
- **After**: `OPENAI_API_KEY`

### AI Model

- **Before**: `claude-3-5-sonnet-20241022`
- **After**: `gpt-4o-mini`

### API Call Structure

We switched back to OpenAI's chat/completions format:

**Before (Anthropic)**:

```python
response = self.client.messages.create(
    model="claude-3-5-sonnet-20241022",
    max_tokens=200,
    temperature=0.7,
    system="System prompt...",
    messages=[
        {"role": "user", "content": "User message..."}
    ]
)
suggested_name = response.content[0].text.strip()
```

**After (OpenAI)**:

```python
response = self.client.chat.completions.create(
    model="gpt-4o-mini",
    max_tokens=200,
    temperature=0.7,
    messages=[
        {"role": "system", "content": "System prompt..."},
        {"role": "user", "content": "User message..."},
    ],
)
suggested_name = response.choices[0].message.content.strip()
```

## Migration Steps for Users

If you were using the Claude-based version, do the following:

1. **Get an OpenAI API Key**
    - Visit <https://platform.openai.com/api-keys>
    - Generate a new API key

2. **Update Environment Variables**
    - Edit your `.env` file
    - Replace `ANTHROPIC_API_KEY` with `OPENAI_API_KEY`
    - Set it to your OpenAI API key

3. **Update Dependencies**

    ```bash
    pip install -r requirements.txt
    ```

4. **Run the Bot**

    ```bash
    python pdf_renamer_bot.py
    ```

## Files Modified

The following files were updated during this migration:

- `ai_renamer.py` - Core AI module
- `requirements.txt` - Dependencies
- `config.example.env` - Configuration template
- `setup.py` - Setup wizard
- `pdf_renamer_bot.py` - Main bot
- `test_basic.py` - Tests
- `README.md` - Documentation
- `ARCHITECTURE.md` - Architecture docs

## Testing

Current status:

- ✅ Module imports
- ✅ Python syntax validation
- ✅ Basic functionality tests
- ✅ Filename sanitization

## Benefits of GPT-4o

- **Lower latency** for shorter chats (mini model)
- **Great instruction following** for structured JSON responses
- **Broad ecosystem support** and easier key management

## Backward Compatibility

This is a breaking change for environments configured with Anthropic. Update your `.env` and reinstall dependencies to continue using the bot.

## Support

For issues or questions about this migration:

1. Check the updated README.md
2. Review the ARCHITECTURE.md
3. Open an issue on GitHub

## Date

Migration completed: February 19, 2026
