from google import genai
from google.genai import types
from app.core.config import get_settings
import logging
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import re

settings = get_settings()
logger = logging.getLogger(__name__)

class GeminiService:
    def __init__(self):
        self.api_key = settings.GEMINI_API_KEY
        self.client = None
        if self.api_key:
            self.client = genai.Client(api_key=self.api_key)
        else:
            logger.warning("GEMINI_API_KEY not found. AI features will be disabled.")

    def _clean_markdown(self, text: str) -> str:
        """Removes excessive markdown tags that might clutter the UI if not rendered as HTML/MD."""
        # Remove bold tags
        text = text.replace('**', '').replace('__', '')
        # Convert markdown links [title](url) to Title: url
        text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1: \2', text)
        return text.strip()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    async def get_ai_intelligence(self, title: str, content: str):
        """
        Generates advanced intelligence: Summary, Research, and Related Sources.
        Uses Gemini 3.1 Flash Lite with Google Search Grounding.
        """
        if not self.client:
            return None

        prompt = f"""
        Article Title: {title}
        Article Content: {content}

        Based on the above article, provide:
        1. A concise, hard-hitting intelligence summary.
        2. Conduct research using Google Search to find latest developments or context.
        3. Provide 3-4 reputable source links (URLs) for further reading.

        IMPORTANT: 
        - DO NOT use Markdown formatting like # or ## headers.
        - Use plain text with clear section names.
        - Ensure source links are full clickable URLs.
        
        Format the output clearly:
        INTELLIGENCE SUMMARY:
        (summary here)

        LATEST DEVELOPMENTS:
        (research here)

        SOURCES:
        - (Title): (URL)
        """

        try:
            google_search_tool = types.Tool(
                google_search=types.GoogleSearch()
            )

            response = self.client.models.generate_content(
                model="gemini-3.1-flash-lite",
                contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[google_search_tool]
                )
            )

            return self._clean_markdown(response.text)
        except Exception as e:
            logger.error(f"Gemini AI Error: {e}")
            raise # Let tenacity retry

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    async def generate_periodic_briefing(self, articles: list, time_of_day: str):
        """
        Generates a highly structured periodic intelligence briefing (Morning/Afternoon/Night).
        Uses clean Markdown formatting.
        """
        if not self.client or not articles:
            return None

        article_texts = []
        for a in articles:
            article_texts.append(f"Source: {a['source']}\nTitle: {a['title']}\nSummary: {a.get('summary', '')}\nScore: {a.get('priority_score', 0)}\n")

        combined_context = "\n---\n".join(article_texts)

        prompt = f"""
        You are a senior intelligence analyst. It is currently the {time_of_day} update.
        Below are the top articles harvested recently:

        {combined_context}

        Provide a cohesive, professional "Situation Report" in Markdown format:

        # 📡 Intelligence Brief: {time_of_day.capitalize()} Edition

        ### 🔝 Top 5 Critical Events
        (List the 5 most important events with a brief high-impact bullet point for each)

        ### 🇮🇳 Main India Focus
        (Deep dive into the most critical narrative/development for India)

        ### 🌍 International Context
        (Highlight any relevant international developments or how they connect to the local context)

        ### 🔍 Key Intelligence Takeaways
        - (Identify 3-4 connecting threads or narrative shifts)

        ### 📎 Verified Context & Sources
        (Provide 2-3 links to related external sources for verified context using Google Search if needed)

        Use standard Markdown (### for headers, - for bullets, **bold** for emphasis).
        DO NOT use # or ## for the section headers, use # for the top title and ### for sections.
        """

        try:
            response = self.client.models.generate_content(
                model="gemini-3.1-flash-lite",
                contents=prompt
            )
            return response.text.strip()
        except Exception as e:
            logger.error(f"Periodic Briefing Error: {e}")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    async def generate_hourly_briefing(self, articles: list):
        """
        Generates a cohesive intelligence briefing from multiple high-priority articles.
        """
        if not self.client or not articles:
            return None

        article_texts = []
        for a in articles:
            article_texts.append(f"Source: {a['source']}\nTitle: {a['title']}\nSummary: {a.get('summary', '')}\n")

        combined_context = "\n---\n".join(article_texts)
        
        prompt = f"""
        You are a senior intelligence analyst. Below are the top articles harvested in the last hour:
        
        {combined_context}
        
        Provide a cohesive Hourly Intelligence Briefing that:
        1. Summarizes the key narrative shifts across these reports.
        2. Highlights the most critical development for national interest.
        3. Identifies connecting threads between these sources.
        4. Add 2-3 links to related external sources for verified context using Google Search if needed.

        IMPORTANT:
        - DO NOT use Markdown headers (# or ##).
        - Use plain text bullet points (-) for takeaways.
        - Ensure URLs are clearly listed.
        """

        try:
            response = self.client.models.generate_content(
                model="gemini-3.1-flash-lite",
                contents=prompt
            )
            return self._clean_markdown(response.text)
        except Exception as e:
            logger.error(f"Briefing Generation Error: {e}")
            raise # Let tenacity retry

gemini_service = GeminiService()
