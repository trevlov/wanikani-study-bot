#!/usr/bin/env python3
"""
WaniKani Study Bot for GitHub Actions - Enhanced Version
Fetches official mnemonics, etymology, and context from WaniKani API
"""

import requests
import os
import sys
import re
from datetime import datetime
from typing import Dict, List, Any, Optional
from twilio.rest import Client

# Configuration from environment variables
API_KEY = os.environ.get('WANIKANI_API_KEY')
TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
TWILIO_FROM_NUMBER = os.environ.get('TWILIO_FROM_NUMBER')
YOUR_PHONE_NUMBER = os.environ.get('YOUR_PHONE_NUMBER')

# WaniKani API settings
BASE_URL = "https://api.wanikani.com/v2"
HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Wanikani-Revision": "20170710"
}

# Study settings
MAX_ITEMS_PER_SESSION = 8
MIN_ACCURACY_THRESHOLD = 75

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
    """Fetch etymology by analyzing components and their meanings."""
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

def create_etymology_string(etymology_info):
    """Create a readable etymology explanation."""
    if not etymology_info:
        return ""
    
    parts = []
    for comp in etymology_info:
        parts.append(f"{comp['component']}({comp['meaning']})")
    
    return " + ".join(parts)

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

def get_struggling_items():
    """Fetch items with low accuracy or current level items if no reviews yet."""
    struggling_items = {
        "radicals": [],
        "kanji": [],
        "vocabulary": []
    }
    
    print("Fetching review statistics...")
    url = f"{BASE_URL}/review_statistics"
    has_reviews = False
    items_processed = 0
    
    while url and items_processed < 500:
        response = requests.get(url, headers=HEADERS)
        if response.status_code != 200:
            print(f"No review statistics found (this is normal for new users)")
            break
            
        data = response.json()
        
        if not data["data"]:
            print("No review data yet")
            break
            
        for stat in data["data"]:
            try:
                # Safely get percentages with defaults
                meaning_percentage = stat["data"].get("meaning_percentage", 100)
                reading_percentage = stat["data"].get("reading_percentage", 100)
                
                # Skip if no actual review data
                if stat["data"].get("meaning_correct", 0) == 0 and stat["data"].get("meaning_incorrect", 0) == 0:
                    continue
                    
                has_reviews = True
                
                if meaning_percentage < MIN_ACCURACY_THRESHOLD or reading_percentage < MIN_ACCURACY_THRESHOLD:
                    subject_id = stat["data"]["subject_id"]
                    subject_data = get_subject_detailed(subject_id)
                    
                    if subject_data:
                        item_info = subject_data.copy()
                        item_info.update({
                            "meaning_percentage": meaning_percentage,
                            "reading_percentage": reading_percentage,
                            "meaning_incorrect": stat["data"].get("meaning_incorrect", 0),
                            "reading_incorrect": stat["data"].get("reading_incorrect", 0),
                            "struggle_score": 0
                        })
                        
                        # Calculate struggle score
                        item_info["struggle_score"] = (
                            (100 - meaning_percentage) + 
                            (100 - reading_percentage) + 
                            (item_info["meaning_incorrect"] * 2) + 
                            (item_info["reading_incorrect"] * 2)
                        )
                        
                        # Categorize
                        if subject_data["object"] == "radical":
                            struggling_items["radicals"].append(item_info)
                        elif subject_data["object"] == "kanji":
                            struggling_items["kanji"].append(item_info)
                        elif subject_data["object"] == "vocabulary":
                            struggling_items["vocabulary"].append(item_info)
                        
                        items_processed += 1
            except KeyError as e:
                # Skip items with missing data
                continue
        
        # Get next page
        url = data["pages"].get("next_url")
        if items_processed >= 100:
            break
    
    # Sort by struggle score
    for category in struggling_items:
        struggling_items[category].sort(key=lambda x: x.get("struggle_score", 0), reverse=True)
    
    total = sum(len(items) for items in struggling_items.values())
    print(f"Found {total} struggling items (has_reviews: {has_reviews})")
    
    return struggling_items, has_reviews

def format_study_item_enhanced(item_type, item, include_full=True):
    """Format a study item with all available helpful information."""
    output = []
    
    # Basic info
    output.append(f"【{item_type.upper()}】 {item['characters']}")
    output.append(f"📖 Meanings: {', '.join(item['meanings'])}")
    
    if item.get('readings'):
        output.append(f"🔊 Readings: {', '.join(item['readings'])}")
    
    # Etymology/Components
    etymology = fetch_etymology_and_components(item)
    if etymology:
        etym_str = create_etymology_string(etymology)
        output.append(f"🧩 Etymology: {etym_str}")
    
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
    """Generate comprehensive study materials from selected items."""
    if not items:
        return None
    
    study_content = []
    sms_content = []
    
    # Full content for logs
    study_content.append(f"📚 WaniKani Study Session - {session_type.title()}")
    study_content.append("=" * 50)
    
    # SMS content (condensed)
    time_greeting = "Good morning!" if session_type == "morning" else "Good evening!"
    sms_content.append(f"📚 WaniKani - {time_greeting}")
    
    if is_new_user:
        study_content.append("Welcome! Here are your new items to learn:\n")
        sms_content.append(f"\n🆕 New items to learn:")
    else:
        study_content.append("Focus on these struggling items:\n")
        sms_content.append(f"\n⚠️ Focus items:")
    
    # Process items for full log
    for i, (item_type, item) in enumerate(items[:MAX_ITEMS_PER_SESSION]):
        study_content.append(f"\n--- Item {i+1} ---")
        study_content.append(format_study_item_enhanced(item_type, item, include_full=True))
    
    # Process items for SMS (condensed)
    for i, (item_type, item) in enumerate(items[:3]):  # Only first 3 for SMS
        sms_content.append(f"\n\n{i+1}. {item['characters']}")
        sms_content.append(f"→ {', '.join(item['meanings'][:2])}")
        
        if item.get('readings'):
            sms_content.append(f"→ {', '.join(item['readings'][:2])}")
        
        # Add etymology
        etymology = fetch_etymology_and_components(item)
        if etymology:
            etym_str = create_etymology_string(etymology)
            sms_content.append(f"→ {etym_str}")
        
        # Add short mnemonic hint
        if item.get('meaning_mnemonic'):
            hint = clean_html_tags(item['meaning_mnemonic'])[:50]
            sms_content.append(f"💭 {hint}...")
        
        # Add accuracy if struggling
        if item.get('meaning_percentage') is not None:
            sms_content.append(f"📊 M:{item['meaning_percentage']}% R:{item.get('reading_percentage', 'N/A')}%")
    
    # Add study tips
    study_content.append("\n" + "=" * 50)
    study_content.append("💡 Study Tips:")
    study_content.append("• Review the etymology to understand character construction")
    study_content.append("• Use the mnemonics from WaniKani")
    study_content.append("• Practice with the context sentences")
    study_content.append("• Write characters by hand for muscle memory")
    
    if len(items) > 3:
        sms_content.append(f"\n\n📚 +{len(items)-3} more items in log")
    
    sms_content.append("\n\n💡 Check GitHub log for full mnemonics!")
    
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

def generate_study_prompt(struggling_items, session_type="morning"):
    """Generate study materials for struggling items."""
    
    selected_items = []
    
    # Prioritize based on session type
    if session_type == "morning":
        priorities = [("kanji", 5), ("radicals", 2), ("vocabulary", 3)]
    else:
        priorities = [("vocabulary", 5), ("kanji", 4), ("radicals", 1)]
    
    for item_type, count in priorities:
        items = struggling_items.get(item_type, [])[:count]
        selected_items.extend([(item_type, item) for item in items])
    
    selected_items = selected_items[:MAX_ITEMS_PER_SESSION]
    
    if not selected_items:
        return None
    
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
    """Main function for GitHub Actions."""
    
    # Determine session type based on time (UTC)
    hour = datetime.utcnow().hour
    session_type = "morning" if hour < 15 else "evening"
    
    print(f"Running {session_type} session at {datetime.utcnow()} UTC")
    
    # Get user info
    user_info = get_user_info()
    if not user_info:
        print("Failed to connect to WaniKani API")
        print("Please check your WANIKANI_API_KEY secret")
        sys.exit(1)
    
    user_level = user_info['level']
    print(f"User: {user_info['username']} (Level {user_level})")
    
    # Get struggling items or current level items
    struggling_items, has_reviews = get_struggling_items()
    
    study_materials = None
    
    # If no struggling items (new user or all items above threshold)
    if not any(struggling_items.values()):
        print("No struggling items found - fetching current level items")
        current_level_items = get_current_level_subjects(user_level)
        
        if any(current_level_items.values()):
            study_materials = generate_study_prompt_new_user(current_level_items)
        else:
            print("No items found to study")
    else:
        # Normal flow for users with review data
        study_materials = generate_study_prompt(struggling_items, session_type)
    
    if study_materials:
        # Send SMS with condensed version
        if send_sms(study_materials["sms_content"]):
            print("✅ SMS sent successfully!")
        else:
            print("❌ SMS sending failed - check Twilio credentials")
        
        # Print full content to GitHub Actions log
        print("\n" + "="*60)
        print("FULL STUDY MATERIALS (saved in GitHub Actions log):")
        print("="*60)
        print(study_materials["full_content"])
        print("="*60)
    else:
        print("No items to study at this time.")
        # Optionally send a motivational message
        msg = f"🎉 Great job! No struggling items (under {MIN_ACCURACY_THRESHOLD}% accuracy) found!\nKeep up the good work on Level {user_level}!"
        send_sms(msg)
    
    print(f"\n✅ Session complete at {datetime.utcnow()} UTC")

if __name__ == "__main__":
    main()
