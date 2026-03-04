/* ==========================================================================
   Billets Monitor MVP — Main JavaScript
   ========================================================================== */

(function () {
  'use strict';

  // ==========================================
  // Toast Notification System
  // ==========================================

  window.showToast = function (message, type) {
    type = type || 'info';
    var container = document.getElementById('toastContainer');
    if (!container) return;

    var icons = {
      success: '✓',
      error: '✕',
      info: 'ℹ',
      warning: '⚠'
    };

    var toast = document.createElement('div');
    toast.className = 'toast toast--' + type;
    toast.innerHTML =
      '<span style="font-size: 1.1rem;">' + (icons[type] || icons.info) + '</span>' +
      '<span>' + message + '</span>';

    container.appendChild(toast);

    // Auto-remove after 4 seconds
    setTimeout(function () {
      toast.classList.add('toast--exit');
      setTimeout(function () {
        if (toast.parentNode) {
          toast.parentNode.removeChild(toast);
        }
      }, 300);
    }, 4000);
  };

  // ==========================================
  // Scroll-based Nav Style
  // ==========================================

  var mainNav = document.getElementById('mainNav');
  if (mainNav) {
    var scrollThreshold = 50;

    function updateNavOnScroll() {
      if (window.scrollY > scrollThreshold) {
        mainNav.classList.add('nav--scrolled');
      } else {
        mainNav.classList.remove('nav--scrolled');
      }
    }

    window.addEventListener('scroll', updateNavOnScroll, { passive: true });
    updateNavOnScroll();
  }

  // ==========================================
  // Active Nav Link on Scroll
  // ==========================================

  var navLinks = document.querySelectorAll('.nav__link[href^="#"]');
  if (navLinks.length > 0) {
    var sections = [];
    navLinks.forEach(function (link) {
      var href = link.getAttribute('href');
      if (href && href.startsWith('#')) {
        var section = document.getElementById(href.substring(1));
        if (section) {
          sections.push({ link: link, section: section });
        }
      }
    });

    function updateActiveNav() {
      var scrollPos = window.scrollY + 200;

      sections.forEach(function (item) {
        var top = item.section.offsetTop;
        var bottom = top + item.section.offsetHeight;

        if (scrollPos >= top && scrollPos < bottom) {
          navLinks.forEach(function (l) { l.classList.remove('nav__link--active'); });
          item.link.classList.add('nav__link--active');
        }
      });
    }

    window.addEventListener('scroll', updateActiveNav, { passive: true });
  }

  // ==========================================
  // Smooth Scroll for Anchor Links
  // ==========================================

  document.addEventListener('click', function (e) {
    var link = e.target.closest('a[href^="#"]');
    if (!link) return;

    var targetId = link.getAttribute('href');
    if (!targetId || targetId === '#') return;

    var target = document.querySelector(targetId);
    if (!target) return;

    e.preventDefault();
    var navHeight = mainNav ? mainNav.offsetHeight : 0;
    var targetPosition = target.getBoundingClientRect().top + window.scrollY - navHeight - 20;

    window.scrollTo({
      top: targetPosition,
      behavior: 'smooth'
    });

    // Close mobile menu if open
    var navLinksEl = document.getElementById('navLinks');
    if (navLinksEl) {
      navLinksEl.classList.remove('nav__links--open');
    }
  });

  // ==========================================
  // Mobile Menu Toggle
  // ==========================================

  var mobileToggle = document.getElementById('mobileToggle');
  var navLinksContainer = document.getElementById('navLinks');

  if (mobileToggle && navLinksContainer) {
    mobileToggle.addEventListener('click', function () {
      navLinksContainer.classList.toggle('nav__links--open');

      // Animate hamburger to X
      var spans = mobileToggle.querySelectorAll('span');
      if (navLinksContainer.classList.contains('nav__links--open')) {
        spans[0].style.transform = 'rotate(45deg) translate(5px, 5px)';
        spans[1].style.opacity = '0';
        spans[2].style.transform = 'rotate(-45deg) translate(5px, -5px)';
      } else {
        spans[0].style.transform = '';
        spans[1].style.opacity = '';
        spans[2].style.transform = '';
      }
    });
  }

  // ==========================================
  // Pricing Toggle (Mois / An)
  // ==========================================

  var billingToggle = document.getElementById('billingToggle');
  if (billingToggle) {
    var isYearly = false;
    var labelMonth = document.getElementById('labelMonth');
    var labelYear = document.getElementById('labelYear');
    var amounts = document.querySelectorAll('.pricing-card__amount');
    var periods = document.querySelectorAll('.pricing-card__period');
    var yearlyDetails = document.querySelectorAll('.pricing-card__yearly-detail');
    var ctaStarter = document.getElementById('ctaStarter');
    var ctaPro = document.getElementById('ctaPro');

    billingToggle.addEventListener('click', function () {
      isYearly = !isYearly;
      billingToggle.classList.toggle('active', isYearly);

      if (labelMonth) labelMonth.classList.toggle('pricing__toggle-label--active', !isYearly);
      if (labelYear) labelYear.classList.toggle('pricing__toggle-label--active', isYearly);

      amounts.forEach(function (el) {
        var price = isYearly ? el.getAttribute('data-yearly') : el.getAttribute('data-monthly');
        el.style.opacity = '0';
        setTimeout(function () {
          el.textContent = price + '\u20AC';
          el.style.opacity = '1';
        }, 150);
      });

      periods.forEach(function (el) {
        var period = isYearly ? el.getAttribute('data-yearly') : el.getAttribute('data-monthly');
        el.textContent = period;
      });

      yearlyDetails.forEach(function (el) {
        el.style.display = isYearly ? 'block' : 'none';
      });

      var billingParam = isYearly ? 'yearly' : 'monthly';
      if (ctaStarter) ctaStarter.href = 'login.html?plan=starter&billing=' + billingParam;
      if (ctaPro) ctaPro.href = 'login.html?plan=pro&billing=' + billingParam;
    });
  }

  // ==========================================
  // FAQ Accordion
  // ==========================================

  var faqItems = document.querySelectorAll('.faq__item');
  faqItems.forEach(function (item) {
    var question = item.querySelector('.faq__question');
    if (!question) return;

    question.addEventListener('click', function () {
      var isOpen = item.classList.contains('faq__item--open');

      // Close all others
      faqItems.forEach(function (otherItem) {
        otherItem.classList.remove('faq__item--open');
        var btn = otherItem.querySelector('.faq__question');
        if (btn) btn.setAttribute('aria-expanded', 'false');
      });

      // Toggle current
      if (!isOpen) {
        item.classList.add('faq__item--open');
        question.setAttribute('aria-expanded', 'true');
      }
    });
  });

  // ==========================================
  // Scroll Reveal Animation
  // ==========================================

  var revealElements = document.querySelectorAll('.reveal');

  if (revealElements.length > 0 && 'IntersectionObserver' in window) {
    var revealObserver = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add('reveal--visible');
            revealObserver.unobserve(entry.target);
          }
        });
      },
      {
        threshold: 0.1,
        rootMargin: '0px 0px -50px 0px'
      }
    );

    revealElements.forEach(function (el) {
      revealObserver.observe(el);
    });
  } else {
    // Fallback: show all immediately
    revealElements.forEach(function (el) {
      el.classList.add('reveal--visible');
    });
  }

  // ==========================================
  // Dashboard: Fetch Stats from API
  // ==========================================

  function fetchDashboardStats() {
    // In production, this would call the real API
    // fetch('/api/stats')
    //   .then(res => res.json())
    //   .then(data => {
    //     document.getElementById('statGmail').textContent = data.gmail_accounts;
    //     document.getElementById('statOrders').textContent = data.orders_found;
    //     document.getElementById('statLastScan').textContent = data.last_scan;
    //   })
    //   .catch(err => {
    //     console.error('Failed to fetch stats:', err);
    //     showToast('Erreur de chargement des stats', 'error');
    //   });
  }

  // Run on dashboard page
  if (document.querySelector('.dashboard')) {
    fetchDashboardStats();
  }

  // ==========================================
  // Keyboard Accessibility
  // ==========================================

  // Allow Enter/Space to activate FAQ items
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' || e.key === ' ') {
      var target = e.target;

      if (target.classList && target.classList.contains('faq__question')) {
        e.preventDefault();
        target.click();
      }
    }
  });

  // ==========================================
  // Escape Key to Close Mobile Menu
  // ==========================================

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      // Close mobile nav
      if (navLinksContainer) {
        navLinksContainer.classList.remove('nav__links--open');
        if (mobileToggle) {
          var spans = mobileToggle.querySelectorAll('span');
          spans[0].style.transform = '';
          spans[1].style.opacity = '';
          spans[2].style.transform = '';
        }
      }

      // Close sidebar on mobile
      var sidebarEl = document.getElementById('sidebar');
      if (sidebarEl) {
        sidebarEl.classList.remove('sidebar--open');
        var overlay = document.getElementById('sidebarOverlay');
        if (overlay) overlay.classList.remove('sidebar-overlay--visible');
      }
    }
  });

})();
