const accountButton = document.querySelector("#accountButton");
const authModal = document.querySelector("#authModal");
const closeAuthModal = document.querySelector("#closeAuthModal");
let currentUser = null;

renderScrapeTypeCards(document.querySelector("#categoryPreviewGrid"), { limit: 12 });

function showAuthModal() {
  authModal.hidden = false;
  document.body.classList.add("modalOpen");
  requestAnimationFrame(() => authModal.classList.add("isVisible"));
}

function hideAuthModal() {
  authModal.classList.remove("isVisible");
  document.body.classList.remove("modalOpen");
  setTimeout(() => { authModal.hidden = true; }, 260);
}

closeAuthModal.addEventListener("click", hideAuthModal);
authModal.addEventListener("click", (event) => {
  if (event.target === authModal) hideAuthModal();
});

accountButton.addEventListener("click", () => {
  if (currentUser) window.location.href = "/account";
  else showAuthModal();
});

async function loadDashboard() {
  const [meResponse, planResponse] = await Promise.all([fetch("/api/me"), fetch("/api/plan")]);
  const me = await meResponse.json();
  const plan = await planResponse.json();
  currentUser = me.authenticated ? me.user : null;
  accountButton.textContent = currentUser ? "My Account" : "Sign in";
  document.querySelector("#currentPlan").textContent = plan.label || "Free";
  document.querySelector("#dashboardPlan").textContent = plan.label || "Free";
  document.querySelector("#dashboardRemaining").textContent =
    plan.plan === "free" ? (plan.free_scrapes_remaining ?? 2) : "Unlimited";
  document.querySelector("#freePlanText").textContent =
    plan.plan === "free" ? `${plan.free_scrapes_remaining ?? 2} preview attempts remaining · 3% data` : "Unlimited scraping active";
  document.querySelector("#dashboardPlanDetail").textContent =
    plan.plan === "free" ? "3% website preview" : "Complete datasets enabled";
}

loadDashboard().catch(() => {});
