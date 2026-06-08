const accountButton = document.querySelector("#accountButton");
const authModal = document.querySelector("#authModal");
const closeAuthModal = document.querySelector("#closeAuthModal");
const statusEl = document.querySelector("#status");
const buttons = [...document.querySelectorAll(".upgradeButton")];
let currentUser = null;
let currentPlan = "free";

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

buttons.forEach((button) => {
  button.addEventListener("click", async () => {
    if (!currentUser) {
      showAuthModal();
      return;
    }
    buttons.forEach((item) => { item.disabled = true; });
    statusEl.textContent = "Opening secure Stripe checkout…";
    try {
      const response = await fetch("/api/checkout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan: button.dataset.plan }),
      });
      const result = await response.json();
      if (!response.ok || !result.checkout_url) throw new Error(result.error || "Could not open checkout.");
      window.location.href = result.checkout_url;
    } catch (error) {
      statusEl.textContent = error.message;
      statusEl.classList.add("error");
      buttons.forEach((item) => { item.disabled = item.dataset.plan === currentPlan; });
    }
  });
});

async function loadPricing() {
  const [meResponse, planResponse] = await Promise.all([fetch("/api/me"), fetch("/api/plan")]);
  const me = await meResponse.json();
  const plan = await planResponse.json();
  currentUser = me.authenticated ? me.user : null;
  currentPlan = plan.plan || "free";
  accountButton.textContent = currentUser ? "My Account" : "Sign in";
  document.querySelector("#currentPlan").textContent = plan.label || "Free";
  document.querySelector("#planFootnote").textContent =
    currentPlan === "free" ? `${plan.free_scrapes_remaining ?? 3} free scrapes remaining` : "Subscription active";
  buttons.forEach((button) => {
    if (button.dataset.plan === currentPlan) {
      button.disabled = true;
      button.textContent = "Current plan";
    }
  });
}

loadPricing().catch(() => {});
