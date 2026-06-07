const scrapeForms = [...document.querySelectorAll(".scrapeForm")];
const scrapeButtons = [...document.querySelectorAll(".scrapeForm button[type='submit']")];
const downloadButton = document.querySelector("#downloadButton");
const statusEl = document.querySelector("#status");
const exportResult = document.querySelector("#exportResult");
const tabs = [...document.querySelectorAll(".tab")];
const tableHead = document.querySelector("#tableHead");
const tableBody = document.querySelector("#tableBody");

let scrapeData = null;
let activeTab = "listings";
let activeScrapeToken = 0;
const sslFallbackWarning =
  /The HTTPS certificate could not be verified by this Python installation,?\s*so the page was fetched with relaxed certificate checking\.?/gi;

const columns = {
  listings: [
    "title",
    "source_category_name",
    "source_category_id",
    "product_id",
    "price",
    "regular_price",
    "sale_price",
    "model_number",
    "brand",
    "sku",
    "mpn",
    "gtin",
    "status",
    "is_available",
    "is_out_of_stock",
    "quantity",
    "weight",
    "category",
    "detail_price",
    "availability",
    "image_count",
    "image",
    "image_1",
    "image_2",
    "image_3",
    "image_4",
    "image_5",
    "all_images",
    "url",
    "detail_status",
    "detail_description",
  ],
  images: ["src", "alt", "title"],
  links: ["href", "text"],
  metadata: ["name", "content"],
};

function setStatus(message, error = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle("error", error);
}

function clearExportResult() {
  exportResult.hidden = true;
  exportResult.innerHTML = "";
}

function setCounts(data) {
  document.querySelector("#listingCount").textContent = data?.counts?.listings ?? 0;
  document.querySelector("#detailCount").textContent = data?.counts?.detail_pages ?? 0;
  document.querySelector("#modelCount").textContent = data?.counts?.model_numbers ?? 0;
  document.querySelector("#categoryCount").textContent = data?.counts?.categories ?? 0;
  document.querySelector("#apiPageCount").textContent = data?.counts?.api_pages ?? 0;
  document.querySelector("#imageCount").textContent = data?.counts?.images ?? 0;
  document.querySelector("#linkCount").textContent = data?.counts?.links ?? 0;
  document.querySelector("#metaCount").textContent = data?.counts?.metadata ?? 0;
}

function cellValue(value, key) {
  if (!value) return "";
  if ((key === "image" || key === "src" || /^image_\d+$/.test(key)) && /^https?:\/\//i.test(value)) {
    return `<a href="${escapeAttr(value)}" target="_blank" rel="noreferrer"><img src="${escapeAttr(value)}" alt=""></a>`;
  }
  if ((key === "url" || key === "href") && /^https?:\/\//i.test(value)) {
    return `<a href="${escapeAttr(value)}" target="_blank" rel="noreferrer">${escapeHtml(value)}</a>`;
  }
  return escapeHtml(String(value));
}

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);
}

function escapeAttr(value) {
  return escapeHtml(String(value));
}

function renderTable() {
  const rows = scrapeData?.[activeTab] ?? [];
  const selectedColumns = columns[activeTab] ?? [];
  tableHead.innerHTML = "";
  tableBody.innerHTML = "";

  if (!rows.length) {
    tableBody.innerHTML = `<tr><td class="empty">No ${activeTab.replace("_", " ")} found yet.</td></tr>`;
    return;
  }

  tableHead.innerHTML = `<tr>${selectedColumns.map((column) => `<th>${escapeHtml(column)}</th>`).join("")}</tr>`;
  tableBody.innerHTML = rows
    .slice(0, 100)
    .map((row) => `<tr>${selectedColumns.map((column) => `<td>${cellValue(row[column], column)}</td>`).join("")}</tr>`)
    .join("");
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function jobStatusText(job) {
  const counts = job.counts || {};
  const bits = [job.message || "Scraping..."];
  if (counts.category_index && counts.categories) {
    bits.push(`Category ${counts.category_index}/${counts.categories}`);
  }
  if (counts.current_category_products && counts.current_category_total) {
    bits.push(`Product ${counts.current_category_products}/${counts.current_category_total}`);
  }
  if (counts.listings) {
    bits.push(`${counts.listings} listings captured`);
  }
  return bits.join(" | ");
}

function finishedStatusText(data) {
  const captured = `Captured ${data?.counts?.listings ?? 0} listings from ${data.page_title || data.url}`;
  const warning = String(data.warning || "").replace(sslFallbackWarning, "").trim();
  if (!warning) return captured;
  return `${captured}. Note: ${warning}`;
}

async function pollScrapeJob(jobId, token) {
  while (true) {
    if (token !== activeScrapeToken) return null;
    const response = await fetch(`/api/job?id=${encodeURIComponent(jobId)}`);
    const job = await response.json();
    if (token !== activeScrapeToken) return null;
    if (!response.ok || job.error) throw new Error(job.error || "Could not read scrape progress");

    setStatus(jobStatusText(job));
    setCounts({ counts: job.counts || {} });

    if (job.status === "complete") return job.data;
    if (job.status === "error") throw new Error(job.error || job.message || "Scrape failed");

    await sleep(1000);
  }
}

tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    activeTab = tab.dataset.tab;
    tabs.forEach((item) => item.classList.toggle("active", item === tab));
    renderTable();
  });
});

scrapeForms.forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const scrapeToken = activeScrapeToken + 1;
    activeScrapeToken = scrapeToken;
    const mode = form.dataset.mode;
    const urlInput = form.querySelector("input[name='url']");
    scrapeButtons.forEach((button) => {
      button.disabled = true;
    });
    downloadButton.disabled = true;
    scrapeData = null;
    clearExportResult();
    setCounts(null);
    renderTable();
    setStatus(mode === "website" ? "Starting whole website scrape..." : "Starting category scrape...");

    try {
      const response = await fetch("/api/scrape", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: urlInput.value.trim(), mode }),
      });
      const start = await response.json();
      if (!response.ok || start.error) throw new Error(start.error || "Scrape failed");

      const data = start.job_id ? await pollScrapeJob(start.job_id, scrapeToken) : start;
      if (!data || scrapeToken !== activeScrapeToken) return;
      scrapeData = data;
      setCounts(data);
      renderTable();
      downloadButton.disabled = false;
      setStatus(finishedStatusText(data));
    } catch (error) {
      if (scrapeToken === activeScrapeToken) setStatus(error.message, true);
    } finally {
      if (scrapeToken === activeScrapeToken) {
        scrapeButtons.forEach((button) => {
          button.disabled = false;
        });
      }
    }
  });
});

downloadButton.addEventListener("click", async () => {
  if (!scrapeData) return;
  downloadButton.disabled = true;
  setStatus("Preparing Excel file...");
  clearExportResult();
  try {
    const response = await fetch("/api/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ data: scrapeData }),
    });
    const result = await response.json();
    if (!response.ok || result.error) throw new Error(result.error || "Could not create Excel file");
    exportResult.hidden = false;
    exportResult.innerHTML = `
      <strong>Excel file saved locally</strong>
      <div class="pathBox">${escapeHtml(result.saved_path)}</div>
      <a href="${escapeAttr(result.download_url)}" target="_blank" rel="noreferrer" download="${escapeAttr(result.filename)}">
        Try browser download: ${escapeHtml(result.filename)}
      </a>
      <span>The Codex in-app browser may block downloads, but the Excel file above has already been created on your Mac.</span>
    `;
    setStatus("Excel file created and saved locally.");
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    downloadButton.disabled = false;
  }
});

renderTable();

const tableWrap = document.querySelector(".tableWrap");
let isDraggingTable = false;
let dragStartX = 0;
let dragStartScrollLeft = 0;

tableWrap.addEventListener("pointerdown", (event) => {
  if (event.target.closest("a, button, input")) return;
  isDraggingTable = true;
  dragStartX = event.clientX;
  dragStartScrollLeft = tableWrap.scrollLeft;
  tableWrap.classList.add("dragging");
  tableWrap.setPointerCapture(event.pointerId);
});

tableWrap.addEventListener("pointermove", (event) => {
  if (!isDraggingTable) return;
  tableWrap.scrollLeft = dragStartScrollLeft - (event.clientX - dragStartX);
});

function stopTableDrag(event) {
  if (!isDraggingTable) return;
  isDraggingTable = false;
  tableWrap.classList.remove("dragging");
  if (tableWrap.hasPointerCapture(event.pointerId)) {
    tableWrap.releasePointerCapture(event.pointerId);
  }
}

tableWrap.addEventListener("pointerup", stopTableDrag);
tableWrap.addEventListener("pointercancel", stopTableDrag);
