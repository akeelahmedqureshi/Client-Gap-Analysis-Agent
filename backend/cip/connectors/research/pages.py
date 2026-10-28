"""What kind of page a URL is, and how much it is worth reading.

Used by the crawler to spend its page budget on product, feature, documentation and pricing pages first, and
by the feature agents to decide which pages can evidence a capability: a keyword in a blog post, a job advert
or a legal page says nothing about what a company offers.
"""

from __future__ import annotations

import re

from cip.core.urls import urlparse

# (kind, path pattern, anchor-text pattern) — first match wins.
# Paths include the common translations (es, fr, de, it, pt, nl), as the analysis supports those languages.
KINDS: list[tuple[str, str, str]] = [
    ("pricing", r"/(pricing|plans|plans-and-pricing|prices?|buy|subscribe|compare-plans|precios|tarifas|planes|prix|"
                r"tarifs|preise|preis|prezzi|piani|precos|preços|planos|prijzen|abonnementen)(/|$|-)",
     r"\b(pricing|plans|prices|precios|tarifas|prix|tarifs|preise|prezzi|preços|prijzen)\b"),
    ("docs", r"/(docs?|documentation|developers?|api|reference|guides?|manual|knowledge-?base|kb|documentacion|"
             r"documentación|dokumentation|documentazione|documentacao|documentação)(/|$|-)",
     r"\b(docs|documentation|developers?|api reference|guides?|documentación|dokumentation)\b"),
    ("help", r"/(help|support|faq|faqs|help-?center|getting-started|onboarding|tutorials?|academy|ayuda|soporte|"
             r"aide|assistance|hilfe|aiuto|supporto|ajuda|suporte|hulp)(/|$|-)",
     r"\b(help|support|faq|getting started|tutorials?|ayuda|soporte|aide|hilfe|aiuto|ajuda)\b"),
    ("integrations", r"/(integrations?|apps|marketplace|connectors?|partners/integrations|integraciones|"
                     r"integrationen|integrazioni|integracoes|integrações|integraties)(/|$|-)",
     r"\b(integrations?|connectors?|integraciones|integrationen|integrazioni)\b"),
    ("features", r"/(features?|capabilities|how-it-works|tour|modules?|functionality|funciones|funcionalidades|"
                 r"caracteristicas|características|fonctionnalites|fonctionnalités|funktionen|merkmale|funzionalita|"
                 r"funzionalità|recursos|functies|como-funciona|comment-ca-marche)(/|$|-)",
     r"\b(features?|capabilities|how it works|product tour|funciones|fonctionnalités|funktionen|funzionalità)\b"),
    ("product", r"/(products?|solutions?|platform|services?|software|use-cases?|industries|for-[a-z-]+|why-[a-z-]+|"
                r"productos?|soluciones|servicios|produits?|solutions|plateforme|produkte?|losungen|lösungen|"
                r"leistungen|prodotti|soluzioni|servizi|produtos?|solucoes|soluções|servicos|serviços|producten|"
                r"oplossingen|diensten)(/|$|-)",
     r"\b(products?|solutions?|platform|services|use cases?|productos|soluciones|servicios|produits|produkte|"
     r"lösungen|prodotti|soluzioni|produtos|soluções)\b"),
    ("customers", r"/(customers?|case-stud(y|ies)|success-stories|testimonials|clients|clientes|casos-de-exito|"
                  r"kunden|referenzen|clienti|klanten)(/|$|-)",
     r"\b(customers|case stud(y|ies)|success stories|clientes|kunden|clienti|klanten)\b"),
    ("about", r"/(about|about-us|company|who-we-are|our-story|team|leadership|sobre-nosotros|quienes-somos|"
              r"nosotros|empresa|a-propos|qui-sommes-nous|entreprise|ueber-uns|uber-uns|über-uns|unternehmen|"
              r"chi-siamo|azienda|sobre|quem-somos|over-ons)(/|$|-)",
     r"\b(about|company|team|nosotros|empresa|à propos|über uns|chi siamo|sobre nós|over ons)\b"),
    ("contact", r"/(contact|contact-us|get-in-touch|locations?|offices?|contacto|contactenos|contactez-nous|kontakt|"
                r"contatti|contato|contatos)(/|$|-)",
     r"\b(contact|get in touch|contacto|kontakt|contatti|contato)\b"),
    ("security", r"/(security|trust|compliance|gdpr|hipaa|soc-?2|seguridad|securite|sécurité|sicherheit|sicurezza|"
                 r"seguranca|segurança|beveiliging)(/|$|-)",
     r"\b(security|trust center|compliance|seguridad|sécurité|sicherheit|sicurezza|segurança)\b"),
    ("news", r"/(blog|news|press|newsroom|articles?|insights|stories|changelog|releases?|updates|events|webinars?|"
             r"noticias|prensa|actualites|actualités|presse|neuigkeiten|aktuelles|notizie|novidades|nieuws)(/|$|-)",
     r"\b(blog|news|press|changelog|noticias|actualités|presse|notizie|nieuws)\b"),
    ("careers", r"/(careers?|jobs|join-us|hiring|vacancies|empleo|trabaja-con-nosotros|carrieres|carrières|"
                r"emplois|karriere|stellenangebote|lavora-con-noi|carreiras|vacatures)(/|$|-)",
     r"\b(careers|jobs|we.re hiring|empleo|karriere|carrières|vacatures)\b"),
    ("legal", r"/(legal|privacy|terms|cookies?|imprint|impressum|gdpr-notice|policies|disclaimer|"
              r"accessibility-statement|privacidad|aviso-legal|terminos|términos|confidentialite|confidentialité|"
              r"mentions-legales|datenschutz|agb|privacy-policy|termini|privacidade|termos|voorwaarden)(/|$|-)",
     r"\b(privacy|terms|cookies?|legal|privacidad|datenschutz|impressum|mentions légales)\b"),
    ("account", r"/(login|log-in|signin|sign-in|signup|sign-up|register|account|cart|checkout|iniciar-sesion|"
                r"registro|connexion|anmelden|registrieren|accedi|entrar)(/|$|-)",
     r"\b(log ?in|sign ?in|sign ?up|iniciar sesión|connexion|anmelden|accedi)\b"),
]
# Pages whose text can evidence a product capability.
CAPABILITY_KINDS = {"home", "product", "features", "docs", "help", "integrations", "pricing", "security", "customers",
                    "about", "other"}
# Pages where a company states what it offers: a capability named there is evidence of the offering.
OFFERING_KINDS = {"home", "product", "features", "docs", "help", "integrations", "pricing"}
# How much reading a page of each kind is worth (crawl order).
PRIORITY = {"product": 10, "features": 10, "pricing": 9, "docs": 8, "integrations": 8, "help": 7, "customers": 6,
            "security": 5, "about": 5, "contact": 5, "other": 3, "news": 1, "careers": 1, "legal": 0, "account": -1}


def kind_of(url: str, anchor: str = "") -> str:
    path = urlparse(url).path.lower() or "/"
    if path in ("", "/"):
        return "home"
    for kind, path_re, _ in KINDS:
        if re.search(path_re, path):
            # A post inside a blog is still news, even if its slug says "features".
            return kind
    low = anchor.lower()
    for kind, _, anchor_re in KINDS:
        if low and re.search(anchor_re, low):
            return kind
    return "other"


def is_listing_noise(url: str) -> bool:
    """Tag, category, author and pagination pages: no new content."""
    path = urlparse(url).path.lower()
    return bool(re.search(r"/(tag|tags|category|categories|author|page)/|/page/\d+|[?&](page|p)=\d+", path + "?" +
                          (urlparse(url).query or "")))


def link_priority(url: str, anchor: str = "") -> int:
    """Crawl priority: capability pages first; deeper pages slightly later; news/careers/legal last."""
    if is_listing_noise(url):
        return -2
    kind = kind_of(url, anchor)
    depth = max(0, urlparse(url).path.strip("/").count("/"))
    score = PRIORITY.get(kind, 3)
    if kind == "news":
        score -= depth  # the blog index is useful (announcements); individual posts much less
    return score - (1 if depth >= 2 and kind not in ("docs", "help", "integrations") else 0)
