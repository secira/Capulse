# WhatsApp and Telegram Setup Guide for Capulse

This guide will help you configure WhatsApp Business API and Telegram Bot for automatically sharing trading signals to your subscribers.

## WhatsApp Business API Setup

### Step 1: Create WhatsApp Business Account
1. Go to [Facebook for Developers](https://developers.facebook.com/)
2. Create a new app and select "Business" as the type
3. Add WhatsApp product to your app

### Step 2: Get API Credentials
1. In the WhatsApp API dashboard, note down:
   - **Access Token** (temporary token for testing)
   - **Phone Number ID** (your WhatsApp Business phone number ID)
   - **Webhook URL** (for receiving messages)

### Step 3: Create WhatsApp Group
1. Create a WhatsApp group for your trading signals
2. Add your WhatsApp Business number to the group
3. Get the group chat ID (you'll need WhatsApp Business API tools for this)

### Step 4: Set Environment Variables
capulse.tech says: Add these to your environment secrets:
```bash
WHATSAPP_API_TOKEN=your_access_token_here
WHATSAPP_PHONE_NUMBER_ID=your_phone_number_id_here
WHATSAPP_GROUP_CHAT_ID=your_group_chat_id_here
```

## Telegram Bot Setup

### Step 1: Create Telegram Bot
1. Open Telegram and search for [@BotFather](https://t.me/botfather)
2. Send `/newbot` command
3. Follow instructions to create your bot
4. Save the **Bot Token** provided

### Step 2: Create Telegram Channel
1. Create a new Telegram channel for trading signals
2. Add your bot as an administrator to the channel
3. Get the channel ID:
   - For public channels: use `@channelname`
   - For private channels: use the numeric ID (e.g., `-1001234567890`)

### Step 3: Set Environment Variables
capulse.tech says: Add these to your environment secrets:
```bash
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHANNEL_ID=@your_channel_name_or_numeric_id
```

## Testing Configuration

### Using the Configuration Script
Run the configuration script to test your setup:
```bash
python config_whatsapp_telegram.py
```

This will show you which platforms are properly configured.

### Test Message Format

**WhatsApp Message Example:**
```
🔔 *Capulse Trading Signal*

📈 *RELIANCE* (Reliance Industries Ltd)

🟢 *Action:* BUY
💰 *Entry Price:* ₹2,840.50
🎯 *Target:* ₹3,100.00
🛑 *Stop Loss:* ₹2,750.00
📊 *Quantity:* 100
⚠️ *Risk:* MEDIUM
⏰ *Time Frame:* SWING

📝 *Analysis:*
Strong breakout above resistance level with high volume...

⏰ Signal Time: 20/08/2024 02:30 PM

_Join Capulse for more signals: https://www.capulse.tech_
```

**Telegram Message Example:**
```
🔔 Capulse Trading Signal

📈 RELIANCE (Reliance Industries Ltd)

🟢 Action: BUY
💰 Entry Price: ₹2,840.50
🎯 Target: ₹3,100.00
🛑 Stop Loss: ₹2,750.00
📊 Quantity: 100
⚠️ Risk: MEDIUM
⏰ Time Frame: SWING
📊 Potential Return: 9.1%

📝 Analysis:
Strong breakout above resistance level with high volume...

⏰ Signal Time: 20/08/2024 02:30 PM

Join Capulse for more signals
```

## Admin Dashboard Integration

Once configured, the admin dashboard will show:
- ✅ Green checkmark for configured platforms
- ❌ Red X for unconfigured platforms
- Auto-sharing options when creating new signals
- Sharing history and delivery status

## Troubleshooting

### WhatsApp Issues
- **403 Forbidden**: Check if your access token is valid
- **Invalid Phone Number**: Ensure phone number ID is correct
- **Group Not Found**: Verify group chat ID and bot permissions

### Telegram Issues
- **Bot Not Found**: Check if bot token is correct
- **Chat Not Found**: Verify channel ID format and bot admin permissions
- **Message Too Long**: Telegram has a 4096 character limit

### Common Solutions
1. **Rate Limits**: Both platforms have rate limits. Don't send too many messages quickly.
2. **Permissions**: Ensure your bot has admin permissions in groups/channels.
3. **Environment Variables**: Double-check all environment variables are set correctly.

## Production Considerations

### WhatsApp Business API
- Free tier has limitations (1000 messages/month)
- Consider upgrading to paid plan for production use
- Set up webhooks for two-way communication

### Telegram Bot
- Free with generous limits
- Consider using inline keyboards for user interaction
- Set up webhooks for real-time message handling

### Security
- Keep API tokens secure and rotate them regularly
- Use environment variables, never hardcode credentials
- Monitor API usage and set up alerts for errors

## Support

If you need help setting up:
1. Check the error logs in the admin dashboard
2. Run the test configuration script
3. Verify all environment variables are set
4. Test with a simple message first

For WhatsApp Business API support: [Meta for Developers](https://developers.facebook.com/support/)
For Telegram Bot API support: [Telegram Bot API Documentation](https://core.telegram.org/bots/api)