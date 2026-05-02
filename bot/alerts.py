"""Rich signal alerts with Apply/Skip buttons."""
from __future__ import annotations

import discord
from discord.ui import View, Button


class OrderApprovalView(View):
    def __init__(self, order_id: int, doc_url: str, timeout: float | None = None):
        super().__init__(timeout=timeout)
        self.order_id = order_id
        self.apply_btn = Button(label="Apply", style=discord.ButtonStyle.success, custom_id=f"apply:{order_id}")
        self.skip_btn = Button(label="Skip", style=discord.ButtonStyle.danger, custom_id=f"skip:{order_id}")
        self.add_item(self.apply_btn)
        self.add_item(self.skip_btn)
        if doc_url:
            self.doc_btn = Button(label="View Doc", style=discord.ButtonStyle.link, url=doc_url)
            self.add_item(self.doc_btn)


def build_signal_embed(*, setup_name: str, tier: str, title: str, budget_text: str,
                       posted_text: str, client_summary: str, why_matched: str,
                       cover_letter_preview: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"Signal: '{setup_name}' (tier: {tier})",
        description=title,
        color=0x00C853 if tier == "critical" else 0x2962FF,
    )
    embed.add_field(name="Budget", value=budget_text, inline=True)
    embed.add_field(name="Posted", value=posted_text, inline=True)
    embed.add_field(name="Client", value=client_summary, inline=False)
    embed.add_field(name="Why matched", value=why_matched, inline=False)
    embed.add_field(name="Cover letter preview", value=cover_letter_preview[:1000], inline=False)
    return embed
