# Migration from OpenAI to Claude (Anthropic)

This document outlines the migration from OpenAI's GPT to Anthropic's Claude for the PDF Renamer Bot.

## Overview

The PDF Renamer Bot has been successfully migrated from using OpenAI's GPT-3.5 to Anthropic's Claude 3.5 Sonnet. This migration provides improved performance and capabilities for AI-powered PDF filename generation.

## What Changed

### Dependencies
- **Before**: `openai>=1.0.0`
- **After**: `anthropic>=0.18.0`

### Environment Variables
- **Before**: `OPENAI_API_KEY`
- **After**: `ANTHROPIC_API_KEY`

### AI Model
- **Before**: `gpt-3.5-turbo`
- **After**: `claude-3-5-sonnet-20241022`

### API Call Structure
The API call structure changed to accommodate Anthropic's message format:

**Before (OpenAI)**:
```python
response = self.client.chat.completions.create(
    model="gpt-3.5-turbo",
    messages=[
        {"role": "system", "content": "System prompt..."},
        {"role": "user", "content": "User message..."}
    ],
    max_tokens=100,
    temperature=0.7
)
suggested_name = response.choices[0].message.content.strip()
```

**After (Anthropic)**:
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

## Migration Steps for Users

If you were using the previous version with OpenAI, follow these steps:

1. **Get Anthropic API Key**
   - Visit https://console.anthropic.com/
   - Create an account if you don't have one
   - Generate an API key

2. **Update Environment Variables**
   - Edit your `.env` file
   - Replace `OPENAI_API_KEY` with `ANTHROPIC_API_KEY`
   - Set the value to your new Anthropic API key

3. **Update Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Run the Bot**
   ```bash
   python pdf_renamer_bot.py
   ```

## Files Modified

The following files were updated:
- `ai_renamer.py` - Core AI module
- `requirements.txt` - Dependencies
- `config.example.env` - Configuration template
- `setup.py` - Setup wizard
- `pdf_renamer_bot.py` - Main bot
- `test_basic.py` - Tests
- `README.md` - Documentation
- `ARCHITECTURE.md` - Architecture docs

## Testing

All tests pass successfully:
- ✅ Module imports
- ✅ Python syntax validation
- ✅ Basic functionality tests
- ✅ Filename sanitization
- ✅ Security scan (CodeQL)
- ✅ Code review

## Benefits of Claude

The migration to Claude provides several benefits:
- **Improved Context Window**: Better handling of longer documents
- **Enhanced Reasoning**: More accurate filename generation
- **Better Instruction Following**: More consistent output format
- **Updated Knowledge**: More recent training data

## Backward Compatibility

This is a breaking change. Users must:
1. Obtain a new Anthropic API key
2. Update their `.env` file
3. Reinstall dependencies

The bot functionality remains the same - only the underlying AI provider has changed.

## Support

For issues or questions about this migration:
1. Check the updated README.md
2. Review the ARCHITECTURE.md
3. Open an issue on GitHub

## Date

Migration completed: February 8, 2026
