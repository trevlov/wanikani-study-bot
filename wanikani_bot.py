#!/usr/bin/env python3
"""
WaniKani Study Bot for GitHub Actions
Generates and sends study prompts via Twilio SMS
"""

import requests
import os
import sys
import random
from datetime import datetime
from typing import Dict, List, Any
from twilio.rest import Client

# Configuration from environment variables
API_KEY = os.environ.get('WANIKANI_API_KEY', 'a4a97af8-0505-4d4d-8d02-72cac191a0d7')
TWILIO_ACCOUNT_SID = os.environ['TWILIO_ACCOUNT_SID']
TWILIO_AUTH_TOKEN = os.environ['TWILIO_AUTH_TOKEN']
TWILIO_FROM_NUMBER = os.environ['TWILIO_FROM_NUMBER']
YOUR_PHONE_NUMBER = os.environ['YOUR_PHONE_NUMBER']

# WaniKani API settings
BASE_URL = "https://api.wanikani.com/v2"
HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Wanikani-Revision": "20170710"
}

# Study settings
MAX_ITEMS_PER_SESSION = 8
MIN_ACCURACY_THRESHOLD = 75

def get_user_info():
    """Get basic user information."""
    response = requests.get(f"{BASE_URL}/user", headers=HEADERS)
    if response.status_code == 200:
        return response.json()["data"]
    return None

def get_subject(subject_id):
    """Get details for a specific subject."""
    response = requests.get(f"{BASE_URL}/subjects/{subject_id}", headers=HEADERS)
    if response.status_code == 200:
        return response.json()["data"]
    return None

def get_struggling_items():
    """Fetch items with low accuracy."""
    struggling_items = {
        "radicals": [],
        "kanji": [],
        "vocabulary": []
    }
    
    print("Fetching struggling items from WaniKani...")
    url = f"{BASE_URL}/review_statistics"
    items_processed = 0
    
    while url and items_processed < 500:  # Limit to prevent timeout
        response = requests.get(url, headers=HEADERS)
        if response.status_code != 200:
            print(f"Error: {response.status_code}")
            break
            
        data = response.json()
        
        for stat in data["data"]:
            meaning_percentage = stat["data"]["meaning_percentage"]
            reading_percentage = stat["data"].get("reading_percentage", 100)
            
            if meaning_percentage < MIN_ACCURACY_THRESHOLD or reading_percentage < MIN_ACCURACY_THRESHOLD:
                subject_id = stat["data"]["subject_id"]
                subject_data = get_subject(subject_id)
                
                if subject_data:
                    item_info = {
                        "id": subject_id,
                        "characters": subject_data.get("characters", subject_data.get("slug", "N/A")),
                        "meanings": [m["meaning"] for m in subject_data.get("meanings", [])],
                        "level": subject_data.get("level", 0),
                        "meaning_percentage": meaning_percentage,
                        "reading_percentage": reading_percentage,
                        "meaning_incorrect": stat["data"]["meaning_incorrect"],
                        "reading_incorrect": stat["data"].get("reading_incorrect", 0),
                        "readings": [],
                        "struggle_score": 0
                    }
                    
                    # Add readings
                    if subject_data["object"] in ["kanji", "vocabulary"]:
                        item_info["readings"] = [r["reading"] for r in subject_data.get("readings", [])]
                    
                    # Calculate struggle score
                    item_info["struggle_score"] = (
                        (100 - meaning_percentage) + 
                        (100 - reading_percentage) + 
                        (meaning_incorrect * 2) + 
                        (stat["data"].get("reading_incorrect", 0) * 2)
                    )
                    
                    # Categorize
                    if subject_data["object"] == "radical":
                        struggling_items["radicals"].append(item_info)
                    elif subject_data["object"] == "kanji":
                        struggling_items["kanji"].append(item_info)
                    elif subject_data["object"] == "vocabulary":
                        struggling_items["vocabulary"].append(item_info)
                    
                    items_processed += 1
        
        # Get next page
        url = data["pages"].get("next_url")
        if items_processed >= 100:  # Stop after finding enough items
            break
    
    # Sort by struggle score
    for category in struggling_items:
        struggling_items[category].sort(key=lambda x: x["struggle_score"], reverse=True)
    
    total = sum(len(items) for items in struggling_items.values())
    print(f"Found {total} struggling items")
    return struggling_items

def generate_llm_prompt(struggling_items, session_type="morning"):
    """Generate an LLM-ready prompt for studying."""
    
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
    
    # Build prompt
    prompt = ["Help me memorize these WaniKani items I'm struggling with:\n\n"]
    
    for item_type, item in selected_items:
        prompt.append(f"【{item_type.upper()}】 {item['characters']}\n")
        prompt.append(f"• Meanings: {', '.join(item['meanings'])}\n")
        
        if item['readings']:
            prompt.append(f"• Readings: {', '.join(item['readings'])}\n")
        
        prompt.append(f"• Accuracy: M{item['meaning_percentage']}% R{item.get('reading_percentage', 'N/A')}%\n\n")
    
    prompt.append("\nCreate memorable mnemonics, stories, and memory techniques for both meanings and readings.")
    
    return "".join(prompt)

def send_sms(message):
    """Send SMS via Twilio."""
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        
        # Truncate if too long
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
    session_type = "morning" if hour < 12 else "evening"
    
    print(f"Running {session_type} session at {datetime.utcnow()}")
    
    # Get user info
    user_info = get_user_info()
    if not user_info:
        print("Failed to connect to WaniKani")
        sys.exit(1)
    
    print(f"User: {user_info['username']} (Level {user_info['level']})")
    
    # Get struggling items
    struggling_items = get_struggling_items()
    
    # Generate prompt
    prompt = generate_llm_prompt(struggling_items, session_type)
    
    if not prompt:
        print("No struggling items found")
        return
    
    # Create SMS
    time_greeting = "Good morning!" if session_type == "morning" else "Good evening!"
    
    sms_message = f"📚 WaniKani Study - {time_greeting}\n\n"
    sms_message += f"Level {user_info['level']} | "
    sms_message += f"{sum(len(items) for items in struggling_items.values())} struggling items\n\n"
    
    # Add top 3 items
    sms_message += "Focus on:\n"
    count = 0
    for category in ["kanji", "vocabulary", "radicals"]:
        for item in struggling_items.get(category, [])[:1]:
            if count >= 3:
                break
            sms_message += f"\n{item['characters']} - {', '.join(item['meanings'][:2])}"
            if item.get('readings'):
                sms_message += f"\n→ {', '.join(item['readings'][:2])}"
            sms_message += f"\n(M:{item['meaning_percentage']}%"
            if item.get('reading_percentage'):
                sms_message += f" R:{item['reading_percentage']}%"
            sms_message += ")\n"
            count += 1
    
    sms_message += "\n💡 Full LLM prompt:"
    
    # Send prompt as second SMS
    if send_sms(sms_message):
        # Send the actual prompt in a follow-up SMS
        prompt_sms = f"COPY THIS FOR CHATGPT/CLAUDE:\n\n{prompt[:1400]}"
        send_sms(prompt_sms)
        
        # Also print full prompt to GitHub Actions log
        print("\n" + "="*50)
        print("FULL LLM PROMPT:")
        print("="*50)
        print(prompt)
        print("="*50)
    
    print("Session complete!")

if __name__ == "__main__":
    main()
