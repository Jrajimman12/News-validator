from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from textblob import TextBlob
import trafilatura
import urllib.parse
import re
import datetime
import httpx
import asyncio

app = FastAPI(title="InfoVerify Fast Engine", version="9.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class Payload(BaseModel):
    url: str
    headline: str

class GrammarPayload(BaseModel):
    text: str

# --- TRUST DATABASES ---
TLD_WEIGHTS = { 
    '.gov': 50, '.mil': 50, '.edu': 45, '.bank': 45, '.int': 45, '.ac.in': 45,
    '.com': 0, '.org': 10, '.net': 0, '.io': 5,
    '.xyz': -20, '.click': -40, '.onion': -50, '.tk': -45
}

TRUSTED_DOMAINS = ['bbc.com', 'reuters.com', 'apnews.com', 'npr.org', 'drmgrdu.ac.in', 'nature.com']
URL_SHORTENERS = ['bit.ly', 'tinyurl.com', 't.co']
CLICKBAIT_PHRASES = ["you won't believe", "shocking", "this one trick", "what happens next", "secret"]
URGENCY_PHRASES = ["act now", "hurry", "limited time", "urgent"]

def extract_domain_info(url_string: str):
    try:
        parsed = urllib.parse.urlparse(url_string if "://" in url_string else "http://" + url_string)
        hostname = parsed.hostname.lower().replace("www.", "")
        parts = hostname.split('.')
        tld = "." + ".".join(parts[-2:]) if len(parts) > 2 and len(parts[-2]) <= 3 else "." + parts[-1]
        return hostname, tld
    except:
        return "", ""

@app.post("/api/v1/grammar")
async def check_grammar(payload: GrammarPayload):
    text = payload.text.strip()
    if len(text.split()) < 3:
        return {"success": True, "corrected": text, "alerts": []}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post("https://api.languagetool.org/v2/check", data={"text": text, "language": "en-US"}, timeout=5.0)
        matches = response.json().get("matches", [])

        alerts, corrected_text, last_end, has_fixes = [], "", 0, False

        for match in matches:
            msg = match["message"]
            if match["replacements"]:
                has_fixes = True
                best_fix = match["replacements"][0]["value"]
                alerts.append(f"Grammar Fix: {msg} (Try: '{best_fix}')")
                start = match["offset"]
                length = match["length"]
                corrected_text += text[last_end:start] + best_fix
                last_end = start + length
            else:
                alerts.append(f"Grammar Fix: {msg}")

        corrected_text += text[last_end:]
        if not has_fixes: corrected_text = text

        return {"success": True, "corrected": corrected_text, "alerts": alerts}
    except:
        return {"success": False, "error": "Grammar checker is offline."}

@app.post("/api/v1/analyze")
async def analyze(payload: Payload):
    headline = payload.headline.strip()
    url = payload.url.lower().strip()
    lower_head = headline.lower()

    source_score, ling_score, form_score = 50, 75, 90
    audit_log = []
    
    # 1. Website Trust Check
    if url:
        hostname, tld = extract_domain_info(url)
        if url.startswith("http://"):
            source_score -= 40
            audit_log.append({"type": "negative", "text": "Website is not secure (uses HTTP instead of HTTPS)."})

        weight = TLD_WEIGHTS.get(tld, 0)
        source_score += weight
        if weight > 0: audit_log.append({"type": "positive", "text": f"Website ending ({tld}) is highly trusted."})
        elif weight < 0: audit_log.append({"type": "negative", "text": f"Website ending ({tld}) is often used for spam."})
        
        if any(hostname.endswith(t) for t in TRUSTED_DOMAINS):
            source_score = 95
            audit_log.append({"type": "positive", "text": "Website is a verified, official news source."})
        elif any(hostname.endswith(u) for u in URL_SHORTENERS):
            source_score = 20
            audit_log.append({"type": "negative", "text": "Link is hidden behind a URL shortener."})

    # 2. Clickbait & Bias Check
    found_clickbait = [p for p in CLICKBAIT_PHRASES if p in lower_head]
    if found_clickbait:
        ling_score -= (20 * len(found_clickbait))
        audit_log.append({"type": "negative", "text": f"Found clickbait words designed to grab attention: '{found_clickbait[0]}'"})

    analysis = TextBlob(headline)
    if analysis.sentiment.subjectivity > 0.6:
        ling_score -= 25
        audit_log.append({"type": "warning", "text": "The writing sounds highly opinionated, not factual."})
    elif analysis.sentiment.subjectivity < 0.3:
        ling_score += 15
        audit_log.append({"type": "positive", "text": "The writing is factual and unbiased."})

    # 3. Writing Quality Check
    if re.search(r'(!!+|\?\?+|\?!|!\?)', headline):
        form_score -= 25
        audit_log.append({"type": "negative", "text": "Unprofessional punctuation detected (e.g., !! or ??)."})

    source_score = max(5, min(98, source_score))
    ling_score = max(5, min(98, ling_score))
    form_score = max(5, min(98, form_score))
    
    final_score = int((source_score * 0.45) + (ling_score * 0.35) + (form_score * 0.20))

    return {
        "validData": True, "finalTrustScore": final_score, 
        "sourceScore": source_score, "lingScore": ling_score, "formScore": form_score, 
        "auditLog": audit_log
    }