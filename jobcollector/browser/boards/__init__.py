"""Browser-based job board scrapers."""
from .indeed import IndeedScraper
from .linkedin import LinkedInScraper
from .glassdoor import GlassdoorScraper
from .google_jobs import GoogleJobsScraper
from .ziprecruiter import ZipRecruiterScraper
from .dice import DiceScraper
from .naukri import NaukriScraper
from .simplyhired import SimplyHiredScraper

SCRAPERS = {
    "indeed": IndeedScraper,
    "linkedin": LinkedInScraper,
    "glassdoor": GlassdoorScraper,
    "google_jobs": GoogleJobsScraper,
    "ziprecruiter": ZipRecruiterScraper,
    "dice": DiceScraper,
    "naukri": NaukriScraper,
    "simplyhired": SimplyHiredScraper,
}

__all__ = ["SCRAPERS"]
