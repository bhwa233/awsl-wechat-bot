"""独立运行 NanoBot + AWSL Gateway Channel"""
from nanobot import NanoBot
from awsl_channel import AWSlGatewayChannel

bot = NanoBot.from_config("config.yaml")
bot.register_channel(AWSlGatewayChannel)
bot.run()
