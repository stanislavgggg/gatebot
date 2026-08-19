"""
Локализация. Ключ верхнего уровня — код ГЕО, "en" — фолбэк.

Тексты для юзера переведены на язык ГЕО. Админка остаётся на русском.
Чтобы добавить язык: скопировать блок и поменять значения.
"""

LOCALES: dict[str, dict[str, str]] = {
    # ----------------------------------------------------------------- LATVIA
    "lv": {
        "gate": (
            "🎁 <b>Visi aktuālie bukmeikeru un kazino bonusi — vienā botā.</b>\n\n"
            "Bezmaksas griezieni, bezdepozīta bonusi, promo kodi un palielinātas "
            "pirmās iemaksas. Saraksts tiek atjaunināts katru nedēļu.\n\n"
            "Lai atvērtu piekļuvi — {n} un nospied «Esmu abonējis»."
        ),
        "gate_one": "abonē kanālu zemāk",
        "gate_many": "abonē abus kanālus zemāk",
        "btn_check": "✅ Esmu abonējis",
        "not_subscribed": (
            "❌ Abonements nav atrasts. Abonē kanālus un nospied pogu vēlreiz."
        ),
        "access_granted": "Piekļuve atvērta ✅",
        "welcome": (
            "✅ <b>Piekļuve atvērta!</b>\n\n"
            "Izvēlies sadaļu — tajā ir aktuālie bonusi ar tiešām saitēm.\n\n"
            "Jaunos bonusus sūtīšu šeit, tiklīdz tie parādīsies."
        ),
        "hub": "🎁 <b>Bonusi</b>\n\nIzvēlies kategoriju:",
        "cat_casino": "🎰 Kazino",
        "cat_betting": "⚽ Likmes",
        "pick_bonus": "Izvēlies bonusu:",
        "btn_claim": "🎁 Saņemt bonusu",
        "btn_back": "⬅️ Atpakaļ",
        "expires": "⏳ Derīgs līdz:",
        "empty": "Šeit pagaidām nav nekā — drīz pievienosim",
        "expired": "Šis bonuss vairs nav aktuāls",
        "geo_pick": "🌍 Izvēlies savu valsti:",
        "start_hint": "Nospied /start",
    },
    # -------------------------------------------------------------- LITHUANIA
    "lt": {
        "gate": (
            "🎁 <b>Visi aktualūs bukmekerių ir kazino bonusai — viename bote.</b>\n\n"
            "Nemokami sukimai, bonusai be depozito, promo kodai ir padidinti "
            "pirmieji depozitai. Sąrašas atnaujinamas kas savaitę.\n\n"
            "Kad atvertum prieigą — {n} ir paspausk «Prenumeravau»."
        ),
        "gate_one": "prenumeruok kanalą žemiau",
        "gate_many": "prenumeruok abu kanalus žemiau",
        "btn_check": "✅ Prenumeravau",
        "not_subscribed": (
            "❌ Prenumerata nerasta. Prenumeruok kanalus ir paspausk mygtuką dar kartą."
        ),
        "access_granted": "Prieiga atverta ✅",
        "welcome": (
            "✅ <b>Prieiga atverta!</b>\n\n"
            "Rinkis skiltį — viduje aktualūs bonusai su tiesioginėmis nuorodomis.\n\n"
            "Naujus bonusus siųsiu čia, kai tik jie atsiras."
        ),
        "hub": "🎁 <b>Bonusai</b>\n\nPasirink kategoriją:",
        "cat_casino": "🎰 Kazino",
        "cat_betting": "⚽ Statymai",
        "pick_bonus": "Pasirink bonusą:",
        "btn_claim": "🎁 Gauti bonusą",
        "btn_back": "⬅️ Atgal",
        "expires": "⏳ Galioja iki:",
        "empty": "Kol kas čia tuščia — netrukus pridėsime",
        "expired": "Šis bonusas nebeaktualus",
        "geo_pick": "🌍 Pasirink savo šalį:",
        "start_hint": "Paspausk /start",
    },
    # ---------------------------------------------------------------- FALLBACK
    "en": {
        "gate": (
            "🎁 <b>All current bookmaker and casino bonuses — in one bot.</b>\n\n"
            "Free spins, no-deposit bonuses, promo codes and boosted first "
            "deposits. The list is updated every week.\n\n"
            "To unlock access — {n} and tap «I subscribed»."
        ),
        "gate_one": "subscribe to the channel below",
        "gate_many": "subscribe to both channels below",
        "btn_check": "✅ I subscribed",
        "not_subscribed": (
            "❌ Subscription not found. Subscribe to the channels and tap again."
        ),
        "access_granted": "Access granted ✅",
        "welcome": (
            "✅ <b>Access granted!</b>\n\n"
            "Pick a section — inside are current bonuses with direct links.\n\n"
            "I'll send new bonuses here as soon as they appear."
        ),
        "hub": "🎁 <b>Bonuses</b>\n\nChoose a category:",
        "cat_casino": "🎰 Casino",
        "cat_betting": "⚽ Betting",
        "pick_bonus": "Choose a bonus:",
        "btn_claim": "🎁 Claim bonus",
        "btn_back": "⬅️ Back",
        "expires": "⏳ Valid until:",
        "empty": "Nothing here yet — coming soon",
        "expired": "This bonus is no longer available",
        "geo_pick": "🌍 Choose your country:",
        "start_hint": "Tap /start",
    },
}

FALLBACK = "en"


def t(geo: str | None, key: str, **kwargs) -> str:
    """Достаёт строку по ГЕО с фолбэком на английский."""
    loc = LOCALES.get((geo or "").lower()) or LOCALES[FALLBACK]
    text = loc.get(key) or LOCALES[FALLBACK].get(key, key)
    return text.format(**kwargs) if kwargs else text
