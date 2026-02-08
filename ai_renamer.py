"""
AI-powered naming module using Anthropic Claude API.
Generates intelligent filenames based on PDF content.
"""
import os
from typing import Optional
from anthropic import Anthropic


class AIRenamer:
    """Uses AI to generate intelligent filenames based on PDF content."""
    
    def __init__(self, api_key: Optional[str] = None, custom_prompt: Optional[str] = None):
        """
        Initialize AI renamer.
        
        Args:
            api_key: Anthropic API key (if not provided, reads from environment)
            custom_prompt: Custom prompt template for naming
        """
        self.api_key = api_key or os.getenv('ANTHROPIC_API_KEY')
        if not self.api_key:
            raise ValueError("Anthropic API key not provided")
        
        self.client = Anthropic(api_key=self.api_key)
        self.custom_prompt = custom_prompt or os.getenv('AI_NAMING_PROMPT')
        self.max_filename_length = int(os.getenv('MAX_FILENAME_LENGTH', '100'))
    
    def generate_filename(self, pdf_content: str, original_filename: str) -> Optional[str]:
        """
        Generate an intelligent filename based on PDF content.
        
        Args:
            pdf_content: Extracted text from the PDF
            original_filename: Original filename for reference
            
        Returns:
            Suggested filename (without extension) or None if generation fails
        """
        if not pdf_content or not pdf_content.strip():
            print("No content provided for filename generation")
            return None
        
        # Truncate content if too long (to save on API costs)
        max_content_length = 3000
        truncated_content = pdf_content[:max_content_length]
        if len(pdf_content) > max_content_length:
            truncated_content += "...[content truncated]"
        
        # Build the prompt
        if self.custom_prompt:
            prompt = f"{self.custom_prompt}\n\nContent:\n{truncated_content}"
        else:
            prompt = f"""Based on the following PDF content, suggest a concise and descriptive filename.
The filename should:
- Be descriptive and meaningful
- Use underscores or hyphens instead of spaces
- Be concise (max {self.max_filename_length} characters)
- Not include the file extension
- Use only alphanumeric characters, underscores, and hyphens

Original filename: {original_filename}

PDF Content:
{truncated_content}

Respond with ONLY the suggested filename, nothing else."""
        
        try:
            response = self.client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=200,
                temperature=0.7,
                system="You are a helpful assistant that generates concise, descriptive filenames based on document content.",
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            
            suggested_name = response.content[0].text.strip()
            
            # Clean up the suggested name
            suggested_name = self._sanitize_filename(suggested_name)
            
            # Enforce length limit
            if len(suggested_name) > self.max_filename_length:
                suggested_name = suggested_name[:self.max_filename_length]
            
            return suggested_name
        
        except Exception as e:
            print(f"Error generating filename with AI: {e}")
            return None
    
    def _sanitize_filename(self, filename: str) -> str:
        """
        Sanitize filename to ensure it's safe for filesystem.
        
        Args:
            filename: Raw filename string
            
        Returns:
            Sanitized filename
        """
        # Remove quotes if present
        filename = filename.strip('"').strip("'")
        
        # Replace problematic characters
        replacements = {
            ' ': '_',
            '/': '-',
            '\\': '-',
            ':': '-',
            '*': '',
            '?': '',
            '"': '',
            '<': '',
            '>': '',
            '|': '-'
        }
        
        for old, new in replacements.items():
            filename = filename.replace(old, new)
        
        # Remove any leading/trailing special characters
        filename = filename.strip('_-.')
        
        return filename
