#!/usr/bin/env python3
"""
WaniKani Study Bot for GitHub Actions - Enhanced Version
Fetches WaniKani data and uses OpenAI for etymology insights
"""

import requests
import os
import sys
import re
import json
from datetime import datetime
from typing import Dict, List, Any, Optional
from twilio.rest import Client
from openai import OpenAI

# Configuration from environment variables
API_KEY = os.environ.get('WANIKANI_API_KEY')
TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
TWILIO_FROM_NUMBER = os.environ.get('TWILIO_FROM_NUMBER')
YOUR_PHONE_NUMBER = os.environ.get('YOUR_PHONE_NUMBER')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')

# Initialize OpenAI client
try:
    if OPENAI_API_KEY:
        openai_client = OpenAI(api_key=OPENAI_API_KEY)
    else:
        print("Warning: OPENAI_API_KEY not found, etymology features will be disabled")
        openai_client = None
except Exception as e:
    print(f"Warning: Could not initialize OpenAI client: {e}")
    print("Etymology features will be disabled")
    openai_client = None

# WaniKani API settings
BASE_URL = "https://api.wanikani.com/v2"
HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Wanikani-Revision": "20170710"
}

# Study settings
MAX_ITEMS_PER_SESSION = 3  # Only top 3 items
MIN_ACCURACY_THRESHOLD = 85  # Higher threshold to catch more mistakes
RECENT_DAYS = 7  # Focus on items reviewed in last 7 days

def clean_html_tags(text):
    """Remove HTML tags from WaniKani mnemonics."""
    if not text:
        return ""
    # Remove common WaniKani HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Clean up extra whitespace
    text = ' '.join(text.split())
    return text

def get_user_info():
    """Get basic user information."""
    response = requests.get(f"{BASE_URL}/user", headers=HEADERS)
    if response.status_code == 200:
        return response.json()["data"]
    return None

def get_subject_detailed(subject_id):
    """Get comprehensive details for a specific subject including mnemonics and context."""
    try:
        response = requests.get(f"{BASE_URL}/subjects/{subject_id}", headers=HEADERS)
        if response.status_code == 200:
            subject = response.json()["data"]
            
            # Extract detailed information
            detailed_info = {
                "id": subject["id"],
                "object": subject["object"],
                "characters": subject["data"].get("characters", subject["data"].get("slug", "N/A")),
                "meanings": [m["meaning"] for m in subject["data"].get("meanings", [])],
                "level": subject["data"].get("level", 0),
                "readings": [],
                "meaning_mnemonic": subject["data"].get("meaning_mnemonic", ""),
                "reading_mnemonic": subject["data"].get("reading_mnemonic", ""),
                "meaning_hint": subject["data"].get("meaning_hint", ""),
                "reading_hint": subject["data"].get("reading_hint", ""),
                "context_sentences": subject["data"].get("context_sentences", []),
                "parts_of_speech": subject["data"].get("parts_of_speech", []),
                "component_subject_ids": subject["data"].get("component_subject_ids", []),
                "amalgamation_subject_ids": subject["data"].get("amalgamation_subject_ids", []),
                "visually_similar_subject_ids": subject["data"].get("visually_similar_subject_ids", []),
                "document_url": subject["data"].get("document_url", "")
            }
            
            # Add readings for kanji and vocabulary
            if subject["object"] in ["kanji", "vocabulary"]:
                detailed_info["readings"] = [r["reading"] for r in subject["data"].get("readings", [])]
                # Get primary reading
                primary_readings = [r["reading"] for r in subject["data"].get("readings", []) if r.get("primary", False)]
                if primary_readings:
                    detailed_info["primary_reading"] = primary_readings[0]
            
            return detailed_info
    except Exception as e:
        print(f"Error fetching subject {subject_id}: {e}")
    return None

def get_subject(subject_id):
    """Get basic details for a specific subject (backward compatibility)."""
    try:
        response = requests.get(f"{BASE_URL}/subjects/{subject_id}", headers=HEADERS)
        if response.status_code == 200:
            return response.json()["data"]
    except:
        pass
    return None

def fetch_etymology_and_components(item):
    """Fetch component breakdown for understanding character construction."""
    etymology_info = []
    
    # If there are component IDs (for kanji/vocabulary)
    if item.get("component_subject_ids"):
        for comp_id in item["component_subject_ids"][:3]:  # Limit to 3 to keep it concise
            comp = get_subject_detailed(comp_id)
            if comp:
                etymology_info.append({
                    "component": comp["characters"],
                    "meaning": comp["meanings"][0] if comp["meanings"] else "",
                    "type": comp["object"]
                })
    
    return etymology_info

def create_component_string(component_info):
    """Create a readable component breakdown."""
    if not component_info:
        return ""
    
    parts = []
    for comp in component_info:
        parts.append(f"{comp['component']}({comp['meaning']})")
    
    return " + ".join(parts)

def get_etymology_from_openai(items_batch):
    """Get etymology insights from OpenAI for a batch of items."""
    if not openai_client:
        print("OpenAI client not available, skipping etymology")
        return {}
        
    try:
        # Prepare the items for OpenAI
        items_text = []
        for item_type, item in items_batch[:5]:  # Limit to first 5 items to manage token usage
            text = f"{item['characters']} ({', '.join(item['meanings'])})"
            if item.get('readings'):
                text += f" - Readings: {', '.join(item['readings'])}"
            items_text.append(text)
        
        prompt = f"""For these Japanese characters/words, provide brief etymology insights (origin, historical development, or interesting linguistic facts). Keep each etymology to 1-2 sentences max:

{chr(10).join(items_text)}

Format as JSON with character as key and etymology as value. Focus on memorable facts that help with learning."""

        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a Japanese language etymology expert. Provide concise, memorable etymology facts that help students remember characters and words."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=500,
            temperature=0.7
        )
        
        # Parse the response
        etymology_text = response.choices[0].message.content
        
        # Try to parse as JSON, or return as dict
        try:
            # Clean the response if it has markdown code blocks
            if "```" in etymology_text:
                etymology_text = etymology_text.split("```")[1]
                if etymology_text.startswith("json"):
                    etymology_text = etymology_text[4:]
            
            etymology_dict = json.loads(etymology_text.strip())
            return etymology_dict
        except:
            # If JSON parsing fails, create a simple dict from the text
            print(f"Could not parse OpenAI response as JSON, using fallback")
            return {}
            
    except Exception as e:
        print(f"OpenAI etymology fetch failed: {e}")
        return {}

def get_current_level_subjects(level):
    """Get all subjects for current level."""
    subjects = {
        "radicals": [],
        "kanji": [],
        "vocabulary": []
    }
    
    print(f"Fetching Level {level} subjects...")
    url = f"{BASE_URL}/subjects?levels={level}"
    
    while url:
        response = requests.get(url, headers=HEADERS)
        if response.status_code != 200:
            break
            
        data = response.json()
        
        for subject in data["data"]:
            item_info = {
                "id": subject["id"],
                "characters": subject["data"].get("characters", subject["data"].get("slug", "N/A")),
                "meanings": [m["meaning"] for m in subject["data"].get("meanings", [])],
                "level": subject["data"].get("level", 0),
                "readings": [],
                "document_url": subject["data"].get("document_url", ""),
                "component_subject_ids": subject["data"].get("component_subject_ids", []),
                "meaning_mnemonic": subject["data"].get("meaning_mnemonic", ""),
                "reading_mnemonic": subject["data"].get("reading_mnemonic", ""),
                "context_sentences": subject["data"].get("context_sentences", []),
                "parts_of_speech": subject["data"].get("parts_of_speech", [])
            }
            
            # Add readings for kanji and vocabulary
            if subject["object"] in ["kanji", "vocabulary"]:
                item_info["readings"] = [r["reading"] for r in subject["data"].get("readings", [])]
            
            # Categorize by type
            if subject["object"] == "radical":
                subjects["radicals"].append(item_info)
            elif subject["object"] == "kanji":
                subjects["kanji"].append(item_info)
            elif subject["object"] == "vocabulary":
                subjects["vocabulary"].append(item_info)
        
        # Check for next page
        url = data["pages"].get("next_url")
        if len(subjects["kanji"]) + len(subjects["radicals"]) + len(subjects["vocabulary"]) >= 30:
            break  # Limit to prevent timeout
    
    return subjects

def get_critical_items():
    """Fetch items in critical condition (Apprentice 1-2, at risk of being forgotten)."""
    critical_items = {
        "radicals": [],
        "kanji": [],
        "vocabulary": []
    }
    
    print("Fetching items in critical condition (Apprentice 1-2)...")
    
    # Get assignments that are in Apprentice 1-2 stages
    url = f"{BASE_URL}/assignments?srs_stages=1,2&unlocked=true"
    critical_assignments = []
    
    while url and len(critical_assignments) < 100:
        response = requests.get(url, headers=HEADERS)
        if response.status_code != 200:
            print("Could not fetch assignments")
            break
            
        data = response.json()
        
        for assignment in data.get("data", []):
            # Only get items that are available for review (not in the future)
            if assignment["data"]["srs_stage"] in [1, 2]:  # Apprentice 1 and 2
                critical_assignments.append({
                    "subject_id": assignment["data"]["subject_id"],
                    "srs_stage": assignment["data"]["srs_stage"],
                    "available_at": assignment["data"]["available_at"]
                })
        
        url = data["pages"].get("next_url")
    
    print(f"Found {len(critical_assignments)} items in Apprentice 1-2")
    
    # Now get review statistics for these critical items
    if critical_assignments:
        # Get review stats to find accuracy
        stats_url = f"{BASE_URL}/review_statistics"
        stats_dict = {}
        
        while stats_url:
            response = requests.get(stats_url, headers=HEADERS)
            if response.status_code != 200:
                break
                
            data = response.json()
            
            for stat in data.get("data", []):
                subject_id = stat["data"]["subject_id"]
                stats_dict[subject_id] = {
                    "meaning_percentage": stat["data"].get("meaning_percentage", 100),
                    "reading_percentage": stat["data"].get("reading_percentage", 100),
                    "meaning_incorrect": stat["data"].get("meaning_incorrect", 0),
                    "reading_incorrect": stat["data"].get("reading_incorrect", 0)
                }
            
            stats_url = data["pages"].get("next_url")
            if len(stats_dict) >= 500:
                break
        
        # Process critical assignments
        for assignment in critical_assignments:
            subject_id = assignment["subject_id"]
            subject_data = get_subject_detailed(subject_id)
            
            if subject_data:
                # Get stats if available
                stats = stats_dict.get(subject_id, {
                    "meaning_percentage": 0,
                    "reading_percentage": 0,
                    "meaning_incorrect": 0,
                    "reading_incorrect": 0
                })
                
                item_info = subject_data.copy()
                item_info.update({
                    "srs_stage": assignment["srs_stage"],
                    "meaning_percentage": stats["meaning_percentage"],
                    "reading_percentage": stats["reading_percentage"],
                    "meaning_incorrect": stats["meaning_incorrect"],
                    "reading_incorrect": stats["reading_incorrect"],
                    "critical_score": 0
                })
                
                # Calculate critical score (lower SRS stage = more critical)
                # Apprentice 1 = most critical, Apprentice 2 = still critical
                srs_weight = 3 if assignment["srs_stage"] == 1 else 2
                
                item_info["critical_score"] = (
                    (srs_weight * 100) +  # SRS stage weight
                    (100 - stats["meaning_percentage"]) +  # Lower accuracy = higher score
                    (100 - stats["reading_percentage"]) + 
                    (stats["meaning_incorrect"] * 2) + 
                    (stats["reading_incorrect"] * 2)
                )
                
                # Categorize
                if subject_data["object"] == "radical":
                    critical_items["radicals"].append(item_info)
                elif subject_data["object"] == "kanji":
                    critical_items["kanji"].append(item_info)
                elif subject_data["object"] == "vocabulary":
                    critical_items["vocabulary"].append(item_info)
    
    # Sort by critical score and take only top items
    for category in critical_items:
        critical_items[category].sort(key=lambda x: x.get("critical_score", 0), reverse=True)
        critical_items[category] = critical_items[category][:3]  # Keep only top 3 per category
    
    total = sum(len(items) for items in critical_items.values())
    print(f"Selected top {total} critical items (Apprentice 1-2)")
    
    return critical_items, total > 0

def format_study_item_enhanced(item_type, item, include_full=True, etymology_dict=None):
    """Format a study item with all available helpful information."""
    output = []
    
    # Basic info
    output.append(f"【{item_type.upper()}】 {item['characters']}")
    output.append(f"📖 Meanings: {', '.join(item['meanings'])}")
    
    if item.get('readings'):
        output.append(f"🔊 Readings: {', '.join(item['readings'])}")
    
    # Component breakdown
    components = fetch_etymology_and_components(item)
    if components:
        comp_str = create_component_string(components)
        output.append(f"🧩 Components: {comp_str}")
    
    # Etymology from OpenAI
    if etymology_dict and item['characters'] in etymology_dict:
        output.append(f"📜 Etymology: {etymology_dict[item['characters']]}")
    
    # Part of speech for vocabulary
    if item.get('parts_of_speech'):
        output.append(f"📝 Type: {', '.join(item['parts_of_speech'])}")
    
    if include_full:
        # Mnemonics (cleaned)
        if item.get('meaning_mnemonic'):
            mnemonic = clean_html_tags(item['meaning_mnemonic'])
            if len(mnemonic) > 200:
                mnemonic = mnemonic[:200] + "..."
            output.append(f"💭 Meaning mnemonic: {mnemonic}")
        
        if item.get('reading_mnemonic'):
            reading_mn = clean_html_tags(item['reading_mnemonic'])
            if len(reading_mn) > 200:
                reading_mn = reading_mn[:200] + "..."
            output.append(f"🗣️ Reading mnemonic: {reading_mn}")
        
        # Context sentence (first one only)
        if item.get('context_sentences') and len(item['context_sentences']) > 0:
            context = item['context_sentences'][0]
            if 'ja' in context:
                output.append(f"📌 Example: {context['ja']}")
                if 'en' in context:
                    output.append(f"   → {context['en']}")
    
    # Accuracy if available
    if item.get('meaning_percentage') is not None:
        output.append(f"📊 Accuracy: M:{item['meaning_percentage']}% R:{item.get('reading_percentage', 'N/A')}%")
    
    return "\n".join(output)

def generate_study_materials(items, session_type="morning", is_new_user=False):
    """Generate comprehensive study materials for top 3 critical items."""
    if not items:
        return None
    
    study_content = []
    sms_content = []
    
    # Get etymology from OpenAI for all items
    print("Fetching etymology from OpenAI...")
    etymology_dict = get_etymology_from_openai(items)
    
    # Full content for logs
    study_content.append(f"📚 WaniKani Critical Items - {session_type.title()}")
    study_content.append(f"⚠️ Items in danger zone (Apprentice 1-2)")
    study_content.append("=" * 50)
    
    # SMS content
    time_greeting = "Good morning!" if session_type == "morning" else "Good evening!"
    sms_content.append(f"⚠️ {time_greeting} - Critical items:")
    
    # Process all 3 items for both log and SMS
    for i, (item_type, item) in enumerate(items):
        srs_stage = "Apprentice 1" if item.get('srs_stage') == 1 else "Apprentice 2"
        
        # Full log version
        study_content.append(f"\n--- CRITICAL ITEM #{i+1} ---")
        study_content.append(f"SRS Stage: {srs_stage} (At risk!)")
        study_content.append(f"Critical Score: {item.get('critical_score', 0)}")
        study_content.append(format_study_item_enhanced(item_type, item, include_full=True, etymology_dict=etymology_dict))
        
        # SMS version (all 3 items)
        sms_content.append(f"\n\n{i+1}. {item['characters']} ({', '.join(item['meanings'][:2])})")
        sms_content.append(f"🔴 {srs_stage}")
        
        if item.get('readings'):
            sms_content.append(f"🔊 {', '.join(item['readings'][:2])}")
        
        # Components
        components = fetch_etymology_and_components(item)
        if components:
            comp_str = create_component_string(components)
            sms_content.append(f"🧩 {comp_str}")
        
        # Etymology from OpenAI
        if etymology_dict and item['characters'] in etymology_dict:
            etym = etymology_dict[item['characters']]
            if len(etym) > 100:
                etym = etym[:100] + "..."
            sms_content.append(f"📜 {etym}")
        
        # Short mnemonic
        if item.get('meaning_mnemonic'):
            hint = clean_html_tags(item['meaning_mnemonic'])[:60]
            sms_content.append(f"💭 {hint}...")
        
        # Accuracy stats if available
        if item.get('meaning_percentage') is not None and item.get('meaning_percentage') > 0:
            sms_content.append(f"📊 M:{item.get('meaning_percentage', 0)}% R:{item.get('reading_percentage', 'N/A')}%")
    
    # Study recommendations
    study_content.append("\n" + "=" * 50)
    study_content.append("🚨 CRITICAL REVIEW STRATEGY:")
    study_content.append("• These items are about to be forgotten!")
    study_content.append("• Review them NOW before they reset to zero")
    study_content.append("• Write each character 10 times")
    study_content.append("• Create personal memory stories")
    study_content.append("• Do your WaniKani reviews ASAP")
    
    sms_content.append("\n\n🚨 Review these NOW!")
    sms_content.append("They're about to reset!")
    
    return {
        "full_content": "\n".join(study_content),
        "sms_content": "\n".join(sms_content)
    }

def generate_study_prompt_new_user(current_level_items):
    """Generate a study prompt for new users without review data."""
    selected_items = []
    
    # Select a mix of items from current level
    for item_type in ["radicals", "kanji", "vocabulary"]:
        items = current_level_items.get(item_type, [])[:3]
        selected_items.extend([(item_type, item) for item in items])
    
    if not selected_items:
        return None
    
    return generate_study_materials(selected_items, session_type="morning", is_new_user=True)

def generate_study_prompt(critical_items, session_type="morning"):
    """Generate study materials for top 3 critical items only."""
    
    all_items = []
    
    # Combine all categories and sort by critical score
    for category in ["kanji", "vocabulary", "radicals"]:
        for item in critical_items.get(category, []):
            all_items.append((category, item))
    
    # Sort all items by critical score and take top 3
    all_items.sort(key=lambda x: x[1].get("critical_score", 0), reverse=True)
    selected_items = all_items[:3]  # Only top 3 items total
    
    if not selected_items:
        return None
    
    print(f"Selected top {len(selected_items)} critical items (Apprentice 1-2)")
    
    return generate_study_materials(selected_items, session_type=session_type, is_new_user=False)

def send_sms(message):
    """Send SMS via Twilio."""
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        
        # Truncate if too long (SMS limit)
        if len(message) > 1500:
            message = message[:1497] + "..."
        
        sms = client.messages.create(
            body=message,
            from_=TWILIO_FROM_NUMBER,
            to=YOUR_PHONE_NUMBER
        )
        
        print(f"SMS sent: {sms.sid}")
        return True
        
    except Exception as e:
        print(f"SMS failed: {e}")
        return False

def main():
    """Main function for GitHub Actions - Focus on critical items (Apprentice 1-2)."""
    
    # Determine session type based on time (UTC)
    hour = datetime.utcnow().hour
    session_type = "morning" if hour < 15 else "evening"
    
    print(f"Running {session_type} session at {datetime.utcnow()} UTC")
    print(f"Checking for items in critical condition (Apprentice 1-2)")
    
    # Get user info
    user_info = get_user_info()
    if not user_info:
        print("Failed to connect to WaniKani API")
        print("Please check your WANIKANI_API_KEY secret")
        sys.exit(1)
    
    user_level = user_info['level']
    print(f"User: {user_info['username']} (Level {user_level})")
    
    # Get critical items (Apprentice 1-2)
    critical_items, has_critical = get_critical_items()
    
    study_materials = None
    
    if has_critical and any(critical_items.values()):
        # Generate study materials for top 3 critical items
        study_materials = generate_study_prompt(critical_items, session_type)
    else:
        print("No items in critical condition (Apprentice 1-2)")
    
    if study_materials:
        # Send SMS with all 3 critical items
        if send_sms(study_materials["sms_content"]):
            print("✅ SMS sent successfully with critical items!")
        else:
            print("❌ SMS sending failed - check Twilio credentials")
        
        # Print full content to GitHub Actions log
        print("\n" + "="*60)
        print("FULL STUDY MATERIALS (Critical Items):")
        print("="*60)
        print(study_materials["full_content"])
        print("="*60)
    else:
        print("No critical items found.")
        # Send motivational message
        msg = f"🎉 Great news! No items in critical condition!\n\nLevel {user_level} | All items are solidly learned!\n\n💪 Keep up the consistent reviews!"
        send_sms(msg)
    
    print(f"\n✅ Session complete at {datetime.utcnow()} UTC")

if __name__ == "__main__":
    main()
