const loading = document.querySelector("#accountLoading");
const content = document.querySelector("#accountContent");
const signedOut = document.querySelector("#accountSignedOut");

function setText(selector, value) {
  document.querySelector(selector).textContent = value;
}

async function loadAccount() {
  try {
    const response = await fetch("/api/account");
    const result = await response.json();
    loading.hidden = true;
    if (!response.ok || !result.account) {
      signedOut.hidden = false;
      return;
    }

    const account = result.account;
    content.hidden = false;
    setText("#accountName", account.name || "Account");
    setText("#accountEmail", account.email || "");
    setText("#accountPlan", account.lifetime_access ? "Lifetime Full Access" : account.label);
    setText("#freeScrapesRemaining", account.lifetime_access ? "Unlimited" : account.free_scrapes_remaining);
    setText(
      "#subscriptionExpiry",
      account.lifetime_access
        ? "Lifetime"
        : account.subscription_expires_label || "Not subscribed",
    );

    const avatar = document.querySelector("#accountAvatar");
    if (account.picture) {
      avatar.src = account.picture;
    } else {
      avatar.hidden = true;
    }

    if (account.lifetime_access) {
      setText("#subscriptionTitle", "Lifetime Full Access");
      setText("#subscriptionDescription", "This Gmail account has permanent access to every scraping feature.");
    } else if (account.plan === "category") {
      setText("#subscriptionTitle", "$5/month Category plan");
      setText("#subscriptionDescription", "Complete category scraping is active for this Google account.");
    } else if (account.plan === "pro") {
      setText("#subscriptionTitle", "$10/month Full Access plan");
      setText("#subscriptionDescription", "Whole website and category scraping are active for this Google account.");
    }
  } catch {
    loading.textContent = "Could not load your account.";
  }
}

loadAccount();
