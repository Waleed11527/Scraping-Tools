const SCRAPE_TYPES = [
  ["static-web-scraping", "Static Web Scraping", "Extract data from normal HTML pages that do not require JavaScript loading."],
  ["dynamic-web-scraping", "Dynamic Web Scraping", "Capture data from pages where listings load after scroll, filters or scripts."],
  ["api-scraping", "API Scraping", "Collect structured data from public website API responses when available."],
  ["browser-automation-scraping", "Browser Automation Scraping", "Use browser-like extraction for pages that need interaction or scrolling."],
  ["screen-scraping", "Screen Scraping", "Read visible page content when structured data is limited."],
  ["cloud-web-scraping", "Cloud Web Scraping", "Run scraping jobs from the hosted app and export results online."],
  ["ai-powered-web-scraping", "AI-Powered Web Scraping", "Detect product-style fields, images and details with smart extraction rules."],
  ["mobile-web-scraping", "Mobile Web Scraping", "Extract data from mobile-friendly web pages and responsive storefronts."],
  ["html-parsing", "HTML Parsing", "Parse page markup for titles, links, images, tables and metadata."],
  ["dom-parsing", "DOM Parsing", "Read structured page elements and product cards from the document tree."],
  ["xpath-scraping", "XPath Scraping", "Target specific page nodes and repeated listing structures."],
  ["regex-scraping", "Regular Expression (Regex) Scraping", "Find patterns such as prices, model numbers, emails and IDs."],
  ["ocr-scraping", "OCR Scraping", "Designed for pages where data appears inside images or visual content."],
  ["real-time-web-scraping", "Real-Time Web Scraping", "Collect fresh listing data whenever you run a new scrape."],
  ["distributed-web-scraping", "Distributed Web Scraping", "Organize larger extraction jobs across many categories or pages."],
  ["incremental-web-scraping", "Incremental Web Scraping", "Use repeat exports to track newly discovered listings over time."],
  ["data-mining-scraping", "Data Mining Scraping", "Build Excel datasets for analysis, comparison and research."],
  ["ecommerce-scraping", "E-commerce Scraping", "Extract product names, prices, sale prices, stock, images and model numbers."],
  ["social-media-scraping", "Social Media Scraping", "Collect public post-style links, titles, metadata and page references."],
  ["search-engine-scraping", "Search Engine Scraping", "Gather search result style links, titles and snippets where allowed."],
  ["news-scraping", "News Scraping", "Extract article lists, headlines, images, links and metadata."],
  ["email-scraping", "Email Scraping", "Find public email patterns from visible website content."],
  ["price-monitoring-scraping", "Price Monitoring Scraping", "Capture current price, regular price, sale price and detail-page price."],
  ["lead-generation-scraping", "Lead Generation Scraping", "Collect public business listings, links, emails and profile data."],
  ["job-board-scraping", "Job Board Scraping", "Extract job listings, titles, companies, links and detail-page information."],
  ["real-estate-scraping", "Real Estate Scraping", "Capture property-style listings, prices, images, locations and details."],
  ["pdf-data-document-scraping", "PDF/Data Document Scraping", "Collect document links and visible document metadata from pages."],
  ["image-scraping", "Image Scraping", "Gather all unique image URLs from listings and detail pages."],
  ["video-metadata-scraping", "Video Metadata Scraping", "Extract public video links, titles, thumbnails and page metadata."],
  ["web-crawling-scraping", "Web Crawling & Scraping", "Follow listing links and crawl product/detail pages for complete exports."],
];

window.SCRAPE_TYPES = SCRAPE_TYPES;

const SCRAPE_TYPE_DETAILS = {
  "static-web-scraping": ["Static page URL", "HTML title, headings, links, images, visible text"],
  "dynamic-web-scraping": ["Dynamic listing URL", "Lazy-loaded cards, scroll results, images, links, prices"],
  "api-scraping": ["API-backed page URL", "Structured product rows, JSON-like fields, API page counts"],
  "browser-automation-scraping": ["Interactive page URL", "Buttons, filters, loaded listings, detail links"],
  "screen-scraping": ["Visible content URL", "Visible text, image references, link labels, layout content"],
  "cloud-web-scraping": ["Hosted scrape URL", "Online job results, Excel-ready rows, metadata"],
  "ai-powered-web-scraping": ["Any data-rich URL", "Smart product fields, model numbers, descriptions, images"],
  "mobile-web-scraping": ["Mobile/responsive URL", "Mobile cards, titles, prices, links, images"],
  "html-parsing": ["HTML page URL", "Meta tags, title, headings, anchors, image sources"],
  "dom-parsing": ["DOM-heavy page URL", "Repeated DOM cards, titles, prices, categories, links"],
  "xpath-scraping": ["Structured page URL", "Repeated elements, detail links, labels, field-like values"],
  "regex-scraping": ["Pattern-rich URL", "Prices, emails, IDs, SKU/model-like patterns"],
  "ocr-scraping": ["Image-heavy page URL", "Image URLs, alt text, captions, visible image metadata"],
  "real-time-web-scraping": ["Fresh listing URL", "Current titles, live prices, stock, updated links"],
  "distributed-web-scraping": ["Large category URL", "Many listings, category counts, detail pages, exports"],
  "incremental-web-scraping": ["Repeat scrape URL", "New listings, changed prices, current availability"],
  "data-mining-scraping": ["Research URL", "Rows for analysis, links, metadata, descriptions"],
  "ecommerce-scraping": ["Store category URL", "Product title, price, sale price, model number, images, stock"],
  "social-media-scraping": ["Public social/content URL", "Post titles, profile links, images, public metadata"],
  "search-engine-scraping": ["Search result URL", "Result titles, snippets, outbound links, metadata"],
  "news-scraping": ["News/category URL", "Headlines, article links, thumbnails, dates, metadata"],
  "email-scraping": ["Contact/listing URL", "Public emails, names, website links, page metadata"],
  "price-monitoring-scraping": ["Product/category URL", "Current price, regular price, sale price, availability"],
  "lead-generation-scraping": ["Business listing URL", "Business names, emails, links, descriptions, profile URLs"],
  "job-board-scraping": ["Job board URL", "Job title, company, location, detail link, description"],
  "real-estate-scraping": ["Property listing URL", "Property title, price, location, images, detail link"],
  "pdf-data-document-scraping": ["Document listing URL", "PDF/document links, titles, file metadata, page links"],
  "image-scraping": ["Gallery/listing URL", "Unique image URLs, alt text, titles, product image counts"],
  "video-metadata-scraping": ["Video listing URL", "Video titles, thumbnails, links, page metadata"],
  "web-crawling-scraping": ["Website or category URL", "Crawled links, listing rows, detail pages, metadata"],
};

window.SCRAPE_TYPE_DETAILS = SCRAPE_TYPE_DETAILS;

function scrapeTypeHref(slug) {
  return `/category?type=${encodeURIComponent(slug)}`;
}

function findScrapeType(slug) {
  return SCRAPE_TYPES.find(([value]) => value === slug) || SCRAPE_TYPES[0];
}

function scrapeTypeDetail(slug) {
  const [value, name, description] = findScrapeType(slug);
  const [urlHint, fields] = SCRAPE_TYPE_DETAILS[value] || ["Target URL", "Titles, links, images, metadata, details"];
  return { slug: value, name, description, urlHint, fields };
}

window.scrapeTypeDetail = scrapeTypeDetail;

function renderScrapeTypeCards(container, options = {}) {
  if (!container) return;
  const limit = options.limit || SCRAPE_TYPES.length;
  container.innerHTML = SCRAPE_TYPES.slice(0, limit).map(([slug, name, description]) => `
    <article class="categoryCard" data-category-card data-name="${escapeScrapeAttr(`${name} ${description}`)}">
      <span>${name.split(" ").slice(0, 2).map((word) => word[0]).join("")}</span>
      <strong>${name}</strong>
      <p>${description}</p>
      <a class="categoryAction" href="${scrapeTypeHref(slug)}">Start scraping</a>
    </article>
  `).join("");
}

function escapeScrapeAttr(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);
}
