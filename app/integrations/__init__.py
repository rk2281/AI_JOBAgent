"""Outbound clients to external services: Gemini today, job sources on Day 6/7.

Nothing outside this package should import a third-party SDK for an
external API directly. A service asks this package for typed data; how
that data was fetched — which provider, which endpoint, which retry
policy — stays here, so swapping a provider never touches a service.
"""
