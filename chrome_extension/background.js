// LeadFlow Job Saver - Background Service Worker

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'autoApply') {
    // Open the LeadFlow auto-apply page
    chrome.tabs.create({
      url: `http://localhost:8000/api/apply?url=${encodeURIComponent(request.url)}`
    });
    sendResponse({ success: true });
  }

  if (request.action === 'saveJob') {
    // Save job via API
    fetch('http://localhost:8000/api/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request.jobData)
    })
    .then(response => response.json())
    .then(data => sendResponse({ success: true, data }))
    .catch(error => sendResponse({ success: false, error: error.message }));

    return true; // Keep message channel open for async response
  }
});

// Add context menu item
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: 'saveToLeadFlow',
    title: 'Save to LeadFlow',
    contexts: ['page', 'link']
  });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === 'saveToLeadFlow') {
    // Get page info and save
    chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => ({
        title: document.title,
        url: window.location.href,
        company: document.querySelector('.company-name, .employer-name')?.textContent || '',
        description: document.querySelector('.job-description, .description')?.textContent?.substring(0, 500) || ''
      })
    }, (results) => {
      if (results && results[0]) {
        const jobData = {
          ...results[0].result,
          source: new URL(results[0].result.url).hostname,
          savedAt: new Date().toISOString()
        };

        fetch('http://localhost:8000/api/jobs', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(jobData)
        });
      }
    });
  }
});
