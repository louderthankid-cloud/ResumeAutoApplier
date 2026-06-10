from playwright.async_api import Browser

from services.catalog_detector import SmartCrawler, choose_best_emails


async def find_contacts(
    start_url: str,
    browser: Browser,
    verbose: bool = False,
) -> dict:
    """
    Запускает SmartCrawler для поиска форм и почт на сайте.
    """
    crawler = SmartCrawler(start_url, verbose=verbose)
    router_decision = await crawler.run(browser, use_llm=True)

    best_emails = choose_best_emails(crawler.emails)
    primary_email = best_emails[0] if best_emails else None

    best_form = max(crawler.findings, key=lambda f: f["score"], default=None)
    form_url = best_form["url"] if best_form else start_url
    form_score = best_form["score"] if best_form else 0
    form_type = best_form["type"] if best_form else "NO_HR_FORMS"

    if router_decision.get("target_url"):
        form_url = router_decision["target_url"]

    return {
        "email": primary_email,
        "all_emails": best_emails,
        "form_url": form_url,
        "score": form_score,
        "type": form_type,
        "decision": router_decision.get("decision", form_type),
        "reasoning": router_decision.get("reasoning", ""),
    }
