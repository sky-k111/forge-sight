function cleanInlineMarkdown(value = "") {
  return String(value)
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*(.*?)\*\*/g, "$1")
    .replace(/__(.*?)__/g, "$1")
    .replace(/^#{1,6}\s*/, "")
    .replace(/[ \t]+/g, " ")
    .trim();
}

function isMarkdownTableRow(line) {
  const trimmed = line.trim();
  return /^\|.*\|$/.test(trimmed) || /^:?-{3,}:?(?:\s*\|\s*:?-{3,}:?)+$/.test(trimmed);
}

function withoutMarkdownTables(lines) {
  const skipped = new Set();
  lines.forEach((line, index) => {
    if (!/^\s*:?-{3,}:?(?:\s*\|\s*:?-{3,}:?)+\s*$/.test(line)) return;
    skipped.add(index);
    if (index > 0 && lines[index - 1].includes("|")) skipped.add(index - 1);
    for (let next = index + 1; next < lines.length && lines[next].includes("|"); next += 1) skipped.add(next);
  });
  return lines.filter((line, index) => !skipped.has(index) && !isMarkdownTableRow(line));
}

function truncateSentence(value, maxLength = 160) {
  const text = cleanInlineMarkdown(value);
  if (text.length <= maxLength) return text;
  const clipped = text.slice(0, maxLength);
  const boundary = Math.max(clipped.lastIndexOf("。"), clipped.lastIndexOf("；"), clipped.lastIndexOf("，"));
  return `${(boundary > maxLength * 0.55 ? clipped.slice(0, boundary + 1) : clipped).trim()}…`;
}

function taskHeading(line) {
  const match = line.match(/^\s*(\d+)\.\s+(?:#{1,6}\s*)?(.+?)\s*$/);
  if (!match) return null;
  return { order: Number(match[1]), title: cleanInlineMarkdown(match[2]) };
}

export function conciseFindingTitle(value, fallback = "分析发现") {
  const title = String(value || fallback)
    .replace(/^\s*\d+[.、]\s*/, "")
    .split(/[。；]/)[0]
    .trim();
  return title.length > 36 ? `${title.slice(0, 35)}…` : title;
}

function sectionConclusion(lines, fallback = "") {
  const visible = withoutMarkdownTables(lines).filter((line) => line.trim());
  const inlineConclusion = visible
    .map((line) => cleanInlineMarkdown(line))
    .find((line) => /^(?:分析)?结论[：:]\s*\S/.test(line));
  if (inlineConclusion) return truncateSentence(inlineConclusion.replace(/^(?:分析)?结论[：:]\s*/, ""));

  const conclusionIndex = visible.findIndex((line) => /^(?:\*\*)?(?:趋势|分析)?结论[：:]?(?:\*\*)?\s*$/.test(line.trim()));
  const bulletsAfterConclusion = visible
    .slice(conclusionIndex >= 0 ? conclusionIndex + 1 : 0)
    .filter((line) => /^\s*[-•]\s+/.test(line))
    .map((line) => cleanInlineMarkdown(line.replace(/^\s*[-•]\s+/, "")));
  if (bulletsAfterConclusion.length) return truncateSentence(bulletsAfterConclusion[0]);

  const bullets = visible
    .filter((line) => /^\s*[-•]\s+/.test(line))
    .map((line) => cleanInlineMarkdown(line.replace(/^\s*[-•]\s+/, "")));
  const compactFacts = bullets.slice(0, 3);
  if (compactFacts.length > 1 && compactFacts.every((item) => item.length <= 32 && /[：:]/.test(item))) {
    return truncateSentence(compactFacts.join("；"));
  }
  if (bullets.length) return truncateSentence(bullets[0]);

  const prose = visible
    .map((line) => cleanInlineMarkdown(line))
    .find((line) => line
      && !/^(?:结果表|图表建议|口径说明|证据|数据不足提示|建议检查)[：:]?$/.test(line)
      && !/^如需/.test(line)
      && !/^\d+\.\s+/.test(line));
  return truncateSentence(prose || fallback || "该项分析已完成，详细结果与证据已保留。");
}

function sectionHasAnalysisEvidence(lines) {
  return lines.some((line) => /^(?:\*\*)?(?:趋势|分析)?结论(?:\*\*)?[：:]?/u.test(line.trim()))
    || lines.some((line) => /^\s*:?-{3,}:?(?:\s*\|\s*:?-{3,}:?)+\s*$/.test(line));
}

export function parseAnalysisNarrative(value = "", { sectionTitles = [] } = {}) {
  const normalized = String(value)
    .replace(/\r\n?/g, "\n")
    .replace(/^[\s]*(?:你好[！!,，。:]?\s*)+/i, "")
    .replace(/(^|[：:。；;])\s+-\s+(?![￥¥$€£]?\s*\d)/g, "$1\n- ")
    .replace(/[ \t]+/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  const rawLines = normalized.split("\n");
  const headingCandidates = rawLines
    .map((line, index) => ({ index, heading: taskHeading(line) }))
    .filter((item) => item.heading);
  const headingIndexes = [];
  for (const candidate of headingCandidates) {
    if (!headingIndexes.length) {
      headingIndexes.push(candidate.index);
      continue;
    }
    const previous = taskHeading(rawLines[headingIndexes.at(-1)]);
    if (candidate.heading.order === previous.order + 1) {
      headingIndexes.push(candidate.index);
      continue;
    }
    if (candidate.heading.order <= previous.order) break;
  }

  const structuredSections = headingIndexes.map((start, index) => {
    const end = headingIndexes[index + 1] ?? rawLines.length;
    return rawLines.slice(start + 1, end);
  });
  const hasAuthoritativeSections = sectionTitles.length >= 2 && sectionTitles.length === headingIndexes.length;

  if (headingIndexes.length >= 2 && (hasAuthoritativeSections || structuredSections.some(sectionHasAnalysisEvidence))) {
    const sections = headingIndexes.map((start, index) => {
      const heading = taskHeading(rawLines[start]);
      const end = headingIndexes[index + 1] ?? rawLines.length;
      return {
        title: conciseFindingTitle(sectionTitles[index], heading.title),
        text: sectionConclusion(rawLines.slice(start + 1, end), heading.title),
      };
    });
    return {
      summary: `已完成 ${sections.length} 项分析，关键结论如下。`,
      highlights: sections,
    };
  }

  const visibleLines = withoutMarkdownTables(rawLines).filter((line) => line.trim());
  const bulletLines = visibleLines.filter((line) => /^\s*[-•]\s+/.test(line));
  const explicitConclusion = visibleLines
    .map((line) => cleanInlineMarkdown(line))
    .find((line) => /^(?:分析)?结论[：:]\s*\S/.test(line));
  const summary = explicitConclusion
    ? explicitConclusion.replace(/^(?:分析)?结论[：:]\s*/, "")
    : visibleLines
    .filter((line) => !/^\s*[-•]\s+/.test(line))
    .map((line) => cleanInlineMarkdown(line))
    .filter((line) => line && !/^(?:结果表|图表建议|口径说明|证据)[：:]?$/.test(line))
    .join(" ")
    .replace(/以下是基于现有数据的简要结论[:：]?\s*$/u, "")
    .trim();
  const highlights = bulletLines.map((line, index) => {
    const text = cleanInlineMarkdown(line.replace(/^\s*[-•]\s+/, ""));
    const separator = text.search(/[:：]/);
    if (separator > 0 && separator < 28) {
      return { title: text.slice(0, separator), text: text.slice(separator + 1).trim() };
    }
    return { title: `发现 ${index + 1}`, text };
  });
  return { summary: truncateSentence(summary, 240), highlights };
}

export function reportListSummary(value = "") {
  const narrative = parseAnalysisNarrative(value);
  const firstFinding = narrative.highlights[0]?.text;
  if (firstFinding && /^已完成 \d+ 项分析/.test(narrative.summary)) return firstFinding;
  return narrative.summary || firstFinding || "尚未形成管理摘要。";
}
