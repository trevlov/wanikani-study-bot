#!/usr/bin/env python3
"""
WaniKani Study Bot for GitHub Actions - Fixed Version
Handles new users and missing review statistics
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
    try:
        response = requests.get(f"{BASE_URL}/subjects/{subject_id}", headers=HEADERS)
        if response.status_code == 200:
            return response.json()["data"]
    except:
        pass
    return None

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
                "document_url": subject["data"].get("document_url", "")
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
                    subject_data = get_subject(subject_id)
                    
                    if subject_data:
                        item_info = {
                            "id": subject_id,
                            "characters": subject_data.get("characters", subject_data.get("slug", "N/A")),
                            "meanings": [m["meaning"] for m in subject_data.get("meanings", [])],
                            "level": subject_data.get("level", 0),
                            "meaning_percentage": meaning_percentage,
                            "reading_percentage": reading_percentage,
                            "meaning_incorrect": stat["data"].get("meaning_incorrect", 0),
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

def generate_study_prompt_new_user(current_level_items):
    """Generate a study prompt for new users without review data."""
    selected_items = []
    
    # Select a mix of items from current level
    for item_type in ["radicals", "kanji", "vocabulary"]:
        items = current_level_items.get(item_type, [])[:3]
        selected_items.extend([(item_type, item) for item in items])
    
    if not selected_items:
        return None
    
    prompt = ["Here are my current WaniKani items to study:\n\n"]
    
    for item_type, item in selected_items[:MAX_ITEMS_PER_SESSION]:
        prompt.append(f"【{item_type.upper()}】 {item['characters']}\n")
        prompt.append(f"• Meanings: {', '.join(item['meanings'])}\n")
        
        if item['readings']:
            prompt.append(f"• Readings: {', '.join(item['readings'])}\n")
        
        prompt.append("\n")
    
    prompt.append("\nPlease help me memorize these with mnemonics and memory techniques!")
    
    return "".join(prompt)

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
        
        if 'meaning_percentage' in item:
            prompt.append(f"• Accuracy: M{item['meaning_percentage']}% R{item.get('reading_percentage', 'N/A')}%\n")
        
        prompt.append("\n")
    
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
    
    user_level = user_info['level']
    print(f"User: {user_info['username']} (Level {user_level})")
    
    # Get struggling items or current level items
    struggling_items, has_reviews = get_struggling_items()
    
    # If no struggling items (new user), get current level items
    if not any(struggling_items.values()):
        print("No review data found - fetching current level items instead")
        current_level_items = get_current_level_subjects(user_level)
        prompt = generate_study_prompt_new_user(current_level_items)
        
        # Create beginner-friendly SMS
        time_greeting = "Good morning!" if session_type == "morning" else "Good evening!"
        
        sms_message = f"""📚 WaniKani Study - {time_greeting}

Welcome to Level {user_level}!

You have {sum(len(items) for items in current_level_items.values())} new items to learn.

Today's focus:"""
        
        # Add first few items
        count = 0
        for category in ["radicals", "kanji", "vocabulary"]:
            for item in current_level_items.get(category, [])[:1]:
                if count >= 3:
                    break
                sms_message += f"\n\n{item['characters']} - {', '.join(item['meanings'][:2])}"
                if item.get('readings'):
                    sms_message += f"\n→ {', '.join(item['readings'][:2])}"
                count += 1
        
        sms_message += "\n\n💡 Keep studying! Reviews will start soon."
        
    else:
        # Normal flow for users with review data
        prompt = generate_llm_prompt(struggling_items, session_type)
        
        time_greeting = "Good morning!" if session_type == "morning" else "Good evening!"
        
        sms_message = f"""📚 WaniKani Study - {time_greeting}

Level {user_level} | {sum(len(items) for items in struggling_items.values())} struggling items

Focus on:"""
        
        # Add top 3 items
        count = 0
        for category in ["kanji", "vocabulary", "radicals"]:
            for item in struggling_items.get(category, [])[:1]:
                if count >= 3:
                    break
                sms_message += f"\n\n{item['characters']} - {', '.join(item['meanings'][:2])}"
                if item.get('readings'):
                    sms_message += f"\n→ {', '.join(item['readings'][:2])}"
                sms_message += f"\n(M:{item.get('meaning_percentage', '?')}%"
                if item.get('reading_percentage'):
                    sms_message += f" R:{item['reading_percentage']}%"
                sms_message += ")"
                count += 1
    
    sms_message += "\n\n💡 Full prompt below:"
    
    # Send messages
    if prompt:
        if send_sms(sms_message):
            # Send the actual prompt in a follow-up SMS
            prompt_sms = f"COPY FOR AI:\n\n{prompt[:1400]}"
            send_sms(prompt_sms)
            
            # Print full prompt to GitHub Actions log
            print("\n" + "="*50)
            print("FULL LLM PROMPT:")
            print("="*50)
            print(prompt)
            print("="*50)
    else:
        # Send just the summary if no prompt
        send_sms(sms_message)
    
    print("Session complete!")

if __name__ == "__main__":
    main()
