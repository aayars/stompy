
            _________                              __
           / _____/ /___________ _____________  __/ /
           \___ \  __/ __ \  __ `__ \  __ \  / / / /
         _____/ / /_/ /_/ / / / / / / /_/ / /_/ /_/
         \_____/\__/\____/_/ /_/ /_/ .___/\__, /_/
                                  /_/   /_____/

Stompy! looks at your Mastodon notifications, and offers to block
spammy accounts and domains. I wrote this in a day, take that as
you may.

Requirements: Python 3, Mastodon.py

1) Clone the repo, change to its directory, and set up a venv:

python3 -m venv venv
source venv/bin/activate
pip install Mastodon.py

2) With a normal user account, create an app in the "Development"
tab of your Mastodon settings.

Give it the following scopes:
read:accounts
read:blocks
read:follows
read:notifications
write:blocks
push
crypto

You will need the access token for the config.

3) Optional: With an admin user account, create an app and give
it the following scopes:

admin:write:accounts
admin:read:domain_blocks
admin:write:domain_blocks
crypto

You will need the access token for the config.

If you don't have an admin user, the script will create user-level
blocks. If you provide an admin key, though, the script will create
instance-level blocks. If your user and admin are the same account,
it's fine to do steps 2 and 3 together.

4) Optional: get an OpenAI API Key. This costs money and some
people may find it offensive, thus it is optional.

5) Copy config.json.distrib to config.json.

cp config.json.distrib config.json

6) Edit config.json. Insert your keys and other info. I recommend
leaving the other settings alone until you know for sure that you
want to change them.

7) Run the script. It does not currently use the streaming API,
and will look only at recent notifications before exiting.
Implementing a streaming listener will happen in a future change.

