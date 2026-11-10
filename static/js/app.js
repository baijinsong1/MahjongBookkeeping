document.addEventListener("submit", function (event) {
    const message = event.target.dataset.confirm;
    if (message && !window.confirm(message)) {
        event.preventDefault();
    }
});

(function initAppNavigation() {
    const stackKey = "majiang:navigation-stack";
    const returnKey = "majiang:returning";
    const guardKey = "majiang:back-guard";
    const currentUrl = window.location.pathname + window.location.search;

    let storage;
    try {
        storage = window.sessionStorage;
        storage.getItem(stackKey);
    } catch (error) {
        return;
    }

    if (document.body.classList.contains("login-body")) {
        storage.removeItem(stackKey);
        storage.removeItem(returnKey);
        return;
    }

    if (!document.querySelector(".auth-bar") || !window.history.pushState) {
        return;
    }

    function readStack() {
        try {
            const value = JSON.parse(storage.getItem(stackKey) || "[]");
            return Array.isArray(value)
                ? value.filter((item) => typeof item === "string")
                : [];
        } catch (error) {
            return [];
        }
    }

    function writeStack(stack) {
        storage.setItem(stackKey, JSON.stringify(stack));
    }

    function internalReferrer() {
        if (!document.referrer) {
            return "";
        }
        try {
            const referrer = new URL(document.referrer);
            if (
                referrer.origin !== window.location.origin ||
                referrer.pathname === "/login" ||
                referrer.pathname.startsWith("/static/")
            ) {
                return "";
            }
            return referrer.pathname + referrer.search;
        } catch (error) {
            return "";
        }
    }

    const returning = storage.getItem(returnKey) === "1";
    storage.removeItem(returnKey);
    let stack = readStack();

    if (!returning) {
        const referrer = internalReferrer();
        if (referrer && referrer !== currentUrl) {
            const referrerIndex = stack.lastIndexOf(referrer);
            if (referrerIndex >= 0) {
                stack = stack.slice(0, referrerIndex + 1);
            } else if (stack[stack.length - 1] !== referrer) {
                stack.push(referrer);
            }
        }

        if (stack[stack.length - 1] !== currentUrl) {
            const currentIndex = stack.lastIndexOf(currentUrl);
            stack =
                currentIndex >= 0
                    ? stack.slice(0, currentIndex + 1)
                    : stack.concat(currentUrl);
        }
    }

    writeStack(stack);

    const currentState =
        window.history.state && typeof window.history.state === "object"
            ? window.history.state
            : {};
    if (!currentState[guardKey]) {
        window.history.pushState(
            Object.assign({}, currentState, { [guardKey]: true }),
            "",
            window.location.href
        );
    }

    let navigatingBack = false;
    window.addEventListener("popstate", function () {
        if (navigatingBack) {
            return;
        }

        let navigationStack = readStack();
        if (navigationStack[navigationStack.length - 1] === currentUrl) {
            navigationStack.pop();
        }

        const target = navigationStack[navigationStack.length - 1];
        if (target && target !== currentUrl) {
            navigatingBack = true;
            writeStack(navigationStack);
            storage.setItem(returnKey, "1");
            window.location.replace(target);
            return;
        }

        writeStack(navigationStack.concat(currentUrl));
        window.history.pushState(
            Object.assign({}, history.state || {}, { [guardKey]: true }),
            "",
            window.location.href
        );
    });
})();
