#!/usr/bin/env python

#
#     _________                              __
#    / _____/ /___________ _____________  __/ /
#    \___ \  __/ __ \  __ `__ \  __ \  / / / /
#  _____/ / /_/ /_/ / / / / / / /_/ / /_/ /_/
#  \_____/\__/\____/_/ /_/ /_/ .___/\__, /_/
#                           /_/   /_____/
#
#    Stompy! Spam mitigation for Mastodon
#

from datetime import datetime, timezone
import json
import time

import requests

from mastodon import Mastodon


# Path to your JSON config file
config_file_path = "config.json"

# Reading the JSON config file
with open(config_file_path, "r") as config_file:
    config = json.load(config_file)

# If True, the relationship must be bidirectional or it's suspicious.
# Supercedes CONNECTED_IF_THEY_FOLLOW_YOU and CONNECTED_IF_YOU_FOLLOW_THEM.
REQUIRE_MUTUAL = config["require_mutual"]

# Considered "connected" if they follow you. Warning: Easy to game if you aren't requiring approval for follows.
CONNECTED_IF_THEY_FOLLOW_YOU = config["connected_if_they_follow_you"]

# Considered "connected" if you follow them
CONNECTED_IF_YOU_FOLLOW_THEM = config["connected_if_you_follow_them"]

# If the account has fewer than this many followers, it's suspicious. Warning: Spoofable with ActivityPub
FOLLOWERS_THRESHOLD = config["followers_threshold"]

# If the account is following fewer than this many others, it's suspicious. Warning: Easy to game
FOLLOWING_THRESHOLD = config["following_threshold"]

# If the account is less than this age, in seconds, it's suspicious. Warning: Spoofable with ActivityPub
ACCOUNT_AGE_THRESHOLD = 60 * 60 * 24 * config["account_age_days_threshold"]

# If True, open signups are considered suspicious.
REQUIRE_CLOSED_SIGNUPS = config["require_closed_signups"]

# If more than this many reasons (heuristics) are determined for a notification, call it spam
REASONS_THRESHOLD = config["reasons_threshold"]

# Mastodon API with user-level access
mastodon = Mastodon(
    access_token=config["mastodon_access_token"],
    api_base_url=config["mastodon_endpoint"]
)

# Mastodon API with administrative access
if config["mastodon_admin_access_token"]:
    admin_mastodon = Mastodon(
        access_token=config["mastodon_admin_access_token"],
        api_base_url=config["mastodon_endpoint"]
    )
else:
    admin_mastodon = None

#
OPENAI_API_KEY = config["openai_api_key"]

# Seconds to sleep between API requests
SLEEP_TIME = 1.25


# Return True if the notification is from a user that we are connected to
def is_connection(notification):
    relationships = mastodon.account_relationships([notification["account"]["id"]])

    if relationships:
        relationship = relationships[0]
        they_follow_you = relationship["following"]
        you_follow_them = relationship["followed_by"]

        # Adjust this based on your criteria for an "existing connection"
        if REQUIRE_MUTUAL:
            return they_follow_you and you_follow_them

        elif CONNECTED_IF_THEY_FOLLOW_YOU:
            return they_follow_you

        elif CONNECTED_IF_YOU_FOLLOW_THEM:
            return you_follow_them

    return False


# Return True if the account is older than the account age threshold
def is_old_account(notification):
    age = datetime.now(timezone.utc) - notification["account"]["created_at"]

    return age.total_seconds() > ACCOUNT_AGE_THRESHOLD


# Return True if the account is following/followed by other accounts
def has_relationships(notification):
    if notification["account"]["followers_count"] > FOLLOWERS_THRESHOLD:
        return True

    if notification["account"]["following_count"] > FOLLOWING_THRESHOLD:
        return True

    return False


# Return True if the instance is already limited. We might be looking at an old notification.
def is_instance_limited(notification):
    if "account" in notification:
        # The 'acct' field contains the username and domain, but only for remote accounts.
        # For local accounts, it contains only the username.
        acct = notification["account"]["acct"]
        
        # If the 'acct' field contains an '@', it's a remote account, and we can extract the domain.
        domain = extract_domain(acct)

        if domain is None:
            # We do not limit the local instance. That would be weird.
            return False
    else:
        # If there's no account information in the notification, return None or handle as needed
        return False

    if admin_mastodon:
        # Admin access required for getting information about instance-level blocks.
        blocks = admin_mastodon.admin_domain_blocks()
    else:
        # Normal account will not have access to admin functions. Use account-level blocks.
        blocks = mastodon.domain_blocks()

    return domain in [instance["domain"] for instance in blocks]


# Return the domain part of a Mastodon account. Returns None for local accounts.
def extract_domain(acct):
    elements = acct.split("@")

    if len(elements) == 1:
        return None
    else:
        return elements[1]


# Get the URLs of any images which were attached to this notification
def extract_image_urls_from_notification(notification):
    urls = []

    # Check if the notification is for a status and has media attachments
    if notification["type"] == "mention" and "media_attachments" in notification["status"]:
        attachments = notification["status"]["media_attachments"]

        if attachments:
            for attachment in attachments:
                urls.append(attachment.get("remote_url"))

    return urls


# Send an image to the OpenAI Vision API to determine its spamminess. Only used in rare instances
# where dumb heuristics were inconclusive.
def describe_image_with_openai_vision_api(url):
    if OPENAI_API_KEY is None:
        return None, None

    payload = {
        "model": "gpt-4-vision-preview",
        "messages": [
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": "We're examining image attachments in potential spam messages. A spam image "
                                "may appear to contain just a URL, or just nonsensical text, or it may even "
                                "contain the word \"SPAM\". Obscene, pornographic, or violent images must "
                                "also be considered spam. Is the image spam? Answer only with \"YES\" or \"NO\", "
                                "followed by your reasoning why or why not the image is spam."
                    },
                ]
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": url
                        }
                    }
                ]
            }
        ],
        "max_tokens": 300,
        "temperature": 0.125
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENAI_API_KEY}"
    }

    response = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)

    if response.status_code == 200:
        content = response.json()["choices"][0]["message"]["content"]

        if content.strip().lower().startswith("yes"):
            return True, content
    else:
        print(f"Failed to get description from OpenAI Vision API. Status code: {response.status_code}; "
              f"Response: {response.text}")

    return None, None


# For readability, summarize the reason GPT gave for suspecting spam.
def format_reason(content):
    if OPENAI_API_KEY is None:
        return content

    payload = {
        "model": "gpt-3.5-turbo",
        "messages": [
            {
                "role": "system",
                "content": "Summarize the given content in complete sentences of 60 characters or fewer. "
                           "If multiple sentences are required, place one sentence per line. Do not exceed "
                           "60 characters per line."
            },
            {
                "role": "user",
                "content": content
            }
        ]
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENAI_API_KEY}"
    }

    response = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)

    return [c for c in response.json()["choices"][0]["message"]["content"].split("\n") if c]


# If dumb heuristics are inconclusive, screen the message with GPT.
def is_content_spammy(notification):
    if OPENAI_API_KEY is None:
        return []

    # Analyze text content using OpenAI"s REST API
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "gpt-4",  # More expensive, but more resilient against e.g. injection attacks
        "messages": [
            {
                "role": "system",
                "content": "Spam may look like traditional spam, but widen your criteria: it's spam if the "
                           "message contains a list of random account names without meaningful context, or "
                           "excessive mentions of other users, or links to social media profiles, or just a "
                           "number, or just a link to a website, or just nonsensical text, or no text at all. "
                           "Obscene or violent messages should also be considered spam. Additionally, any "
                           "message that seems to serve no purpose other than to advertise or draw unsolicited "
                           "attention can be considered spam. Is the message spam? Answer only with \"YES\" or "
                           "\"NO\", followed by your reasoning why or why not the message is spam."

            },
            {
                "role": "user",
                "content": notification['status']['content'],
            }
        ],
        "temperature": 0.125
    }

    response = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
    content = response.json()["choices"][0]["message"]["content"]

    time.sleep(SLEEP_TIME)

    reasons = []

    if content.strip().lower().startswith("yes"):
        reasons += format_reason(content)

    time.sleep(SLEEP_TIME)

    for url in extract_image_urls_from_notification(notification):
        is_spam, content = describe_image_with_openai_vision_api(url)

        if is_spam:
            reasons += format_reason(content)

        time.sleep(SLEEP_TIME)

    return reasons


# Check the remote instance's API to see if they're allowing open registrations. API access is
# contingent on how the remote instance is set up, this might not always work. It's also easily
# spoofable by ActivityPub.
def has_open_registration(instance_url):
    # Ensure the instance URL ends with a slash
    if not instance_url.endswith('/'):
        instance_url += '/'

    # Construct the URL to the instance's API endpoint
    api_url = f"https://{instance_url}api/v2/instance"

    try:
        response = requests.get(api_url)
        response.raise_for_status()  # Raise an exception for HTTP error responses

        # Parse the JSON response
        instance_info = response.json()

        # Check if registrations are open
        registrations_open = instance_info.get('registrations', False)

        return registrations_open

    except requests.RequestException as e:
        return None


# Try to determine, with dumb heuristics, if a received notification is probable spam.
# Fall back to AI in rare cases where it's not obvious.
def is_spam(notification):
    reasons = []

    #
    if is_connection(notification):
        print(f"✅ Message is from a friend. Hello, friend!")
        return False  # Friends are never considered spammers. For now. ゴゴゴゴ
    else:
        reasons.append("Message is from someone who is not a connection")

    #
    domain = extract_domain(notification["account"]["acct"])

    if domain:
        if REQUIRE_CLOSED_SIGNUPS and has_open_registration(domain):
            reasons.append("Message is from an instance with open registration")
    else:
        print(f"{acct} does not have a domain")
        print(f"✅ Message is from local account. Deferring to human judgement.")
        return False  # For now. ゴゴゴゴ

    #
    if is_old_account(notification):
        print("✅ Message is not from a new account")
    else:
        reasons.append("Message is from a new account")

    #
    if has_relationships(notification):
        print("✅ Message sender has followers and/or is following others")
    else:
        reasons.append("Message is from an account with no or few relationships")

    #
    if len(reasons) <= REASONS_THRESHOLD:  # We're on the cusp, based on low-hanging fruit heuristics. Bring in the AI.
        reasons += is_content_spammy(notification)

    if len(reasons) > REASONS_THRESHOLD:
        return reasons

    #
    return []


def get_user_choice(prompt):
    # Valid choices dictionary for easy expansion or modification
    valid_choices = {
        'Y': 'Yes',
        'N': 'No',
        # 'A': 'All'
    }

    # Specify the default choice (make sure its case matches the dict key)
    default_choice = 'Y'

    # Prompt message including dynamically generated choices and the default choice
    prompt_message = f"{prompt} (Y/n)"

    while True:
        # Ask the user for their choice, making it case insensitive
        user_input = input(prompt_message).strip().upper() or default_choice

        # Check if the input is one of the valid choices
        if user_input in valid_choices:
            return user_input == "Y"


def block_domain(domain):
    if admin_mastodon:
        # If we have an admin key, block it on the instance level
        admin_mastodon.admin_create_domain_block(
            domain=domain,
            severity='silence',
            reject_media=True,
            reject_reports=True,
            private_comment="Blocked by Stompy for spam",
            obfuscate=True
        )

        print("🔨 Instance has been limited.")

    else:
        # Block just on the account level
        mastodon.domain_block(domain)

        print("🔨 Instance has been blocked.")


def block_account(account_id):
    if admin_mastodon:
        admin_mastodon.admin_account_moderate(
            account_id, 
            action="suspend",
            send_email_notification=False
        )

        print("🔨 Account has been suspended.")

    else:
        mastodon.account_block(account_id)

        print("🔨 Account has been blocked.")


def main():
    notifications = mastodon.notifications()

    for notification in notifications:
        if notification["type"] == "mention":
            acct = notification["account"]["acct"]
            domain = extract_domain(acct)

            print("")
            reasons = is_spam(notification)

            if reasons:
                for reason in reasons:
                    print(f"❌ {reason}")

                account_id = notification["account"]["id"]

                if admin_mastodon:
                    block_or_suspend = "suspending"
                else:
                    block_or_suspend = "blocking"

                if is_instance_limited(notification):
                    print(f"⚠️  Recommend {block_or_suspend} {acct}")

                    if get_user_choice(f"Proceed with {block_or_suspend} account {acct} (ID {account_id})?"):
                        block_account(account_id)

                else:
                    if admin_mastodon:
                        block_or_limit = "limiting"
                    else:
                        block_or_limit = "blocking"

                    print(f"⚠️  Recommend {block_or_suspend} {acct} and {block_or_limit} instance {domain}")

                    if get_user_choice(f"Proceed with {block_or_suspend} account {acct} (ID {account_id})?"):
                        block_account(account_id)

                    if get_user_choice(f"Proceed with {block_or_limit} instance {domain}?"):
                        block_domain(domain)

            else:
                print(f"😊 Friendly message from {acct}")

            time.sleep(SLEEP_TIME)


if __name__ == "__main__":
    main()

#
# Copyright 2024 Alex Ayars
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
