/**
 * Parses a unified git diff and reconstructs original and modified
 * content streams for Monaco DiffEditor.
 */
export interface ReconstructedDiff {
  original: string;
  modified: string;
}

export function parseUnifiedDiff(diffText: string): ReconstructedDiff {
  if (!diffText || typeof diffText !== "string") {
    return { original: "", modified: "" };
  }

  const lines = diffText.split(/\r?\n/);
  const originalLines: string[] = [];
  const modifiedLines: string[] = [];

  let inHunk = false;

  for (const line of lines) {
    // Skip git diff headers
    if (
      line.startsWith("diff --git") ||
      line.startsWith("index ") ||
      line.startsWith("--- ") ||
      line.startsWith("+++ ") ||
      line.startsWith("new file mode") ||
      line.startsWith("deleted file mode")
    ) {
      continue;
    }

    // Hunk header e.g. @@ -1,7 +1,7 @@
    if (line.startsWith("@@")) {
      inHunk = true;
      continue;
    }

    if (!inHunk) {
      continue;
    }

    if (line.startsWith("-")) {
      originalLines.push(line.slice(1));
    } else if (line.startsWith("+")) {
      modifiedLines.push(line.slice(1));
    } else if (line.startsWith(" ")) {
      originalLines.push(line.slice(1));
      modifiedLines.push(line.slice(1));
    } else if (line.startsWith("\\ No newline at end of file")) {
      // ignore
      continue;
    } else {
      // Fallback context line
      originalLines.push(line);
      modifiedLines.push(line);
    }
  }

  // If the diff was not in standard unified diff format (e.g. raw text),
  // return original as empty and modified as the full text.
  if (originalLines.length === 0 && modifiedLines.length === 0) {
    return {
      original: "",
      modified: diffText,
    };
  }

  return {
    original: originalLines.join("\n"),
    modified: modifiedLines.join("\n"),
  };
}
