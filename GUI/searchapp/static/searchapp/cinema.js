(function (global) {
	"use strict";
	function esc(value) {
		return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
			return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
		});
	}

	function duotone(text) {
		var hue = 0, s = String(text || "?");
		for (var i = 0; i < s.length; i++) hue += s.charCodeAt(i);
		hue = hue % 360;
		return ["hsl(" + hue + " 34% 26%)", "hsl(" + hue + " 40% 10%)"];
	}

	function titleize(text) {
		return String(text == null ? "" : text)
			.replace(/[-_]+/g, " ")
			.replace(/\s+/g, " ")
			.trim()
			.split(" ")
			.map(function (w) {
				var lower = w === w.toLowerCase() && w !== w.toUpperCase();
				return lower ? w.charAt(0).toUpperCase() + w.slice(1) : w;
			})
			.join(" ");
	}

	function pct(value) {
		var n = parseFloat(value);
		return isFinite(n) ? Math.max(0, Math.min(100, n)) : 0;
	}

	function paint(node, key) {
		var tone = duotone(key);
		node.style.setProperty("--cn-p1", tone[0]);
		node.style.setProperty("--cn-p2", tone[1]);
	}

	global.CN = { esc: esc, duotone: duotone, titleize: titleize, pct: pct, paint: paint };
})(window);
