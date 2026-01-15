"""Slack CLI - Main entry point."""

import typer

# Import command modules
from .commands import server, client, inbox, resolve, message, write

# Create main app
app = typer.Typer(help="Slack CLI via browser-use")

# Add subcommands
app.add_typer(server.app, name="server")
app.add_typer(client.app, name="client")
app.add_typer(inbox.app, name="inbox")
app.add_typer(resolve.channel_app, name="channel")
app.add_typer(resolve.user_app, name="user")
app.add_typer(message.app, name="message")

# Add top-level commands from write module
app.command("pull")(write.pull_command)
app.command("reply")(write.reply_command)
app.command("react")(write.react_command)
app.command("mute")(write.mute_command)


def main():
    """Main entry point."""
    app()


if __name__ == "__main__":
    main()
