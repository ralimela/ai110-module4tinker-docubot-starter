"""
Core DocuBot class responsible for:
- Loading documents from the docs/ folder
- Building a simple retrieval index (Phase 1)
- Retrieving relevant snippets (Phase 1)
- Supporting retrieval only answers
- Supporting RAG answers when paired with Gemini (Phase 2)
"""

import os
import glob
import re

# Very common words that appear in almost every document.
# They don't help tell documents apart, so we skip them when scoring.
STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "be", "to", "of", "in", "on",
    "for", "and", "or", "it", "this", "that", "with", "as", "by", "at",
    "do", "does", "i", "my", "how", "what", "where", "which", "when",
    "who", "can", "should", "there", "from", "if", "any", "these",
}

# Common word endings to strip so different forms of a word match.
# Checked in this order; only the first matching ending is removed.
SUFFIXES = ["ing", "ion", "ed", "es", "e", "s"]


def normalize_word(word):
    """
    Strip one common ending so word forms match each other:
      generated / generation / generate -> 'generat'
      connect / connection / connecting -> 'connect'
      users -> 'user', endpoints -> 'endpoint'
    We only strip if at least 4 letters remain, so short words
    like 'api' or 'use' are left alone.
    """
    for suffix in SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def split_words(text):
    """
    Lowercase the text and split it into raw words.
    Anything that isn't a letter or digit is a separator, so punctuation
    is dropped and 'auth_utils' becomes 'auth' + 'utils'.
    """
    return re.findall(r"[a-z0-9]+", text.lower())


def tokenize(text):
    """
    Turn document text into normalized words.
    Example: "Tokens are Generated" -> ["token", "are", "generat"]
    """
    return [normalize_word(w) for w in split_words(text)]


def query_words(query):
    """
    The meaningful words of a question: drop stopwords, normalize the rest,
    and remove duplicates.
    Example: "Where is the auth token generated?" -> {"auth", "token", "generat"}
    """
    return {normalize_word(w) for w in split_words(query) if w not in STOPWORDS}


def split_into_sections(text):
    """
    Chunking rule: start a new chunk at every Markdown heading line
    (a line beginning with '#', e.g. '## Token Generation').
    Each chunk = one heading + the text under it, up to the next heading.

    Our docs are organized by heading, one topic per section, so this
    keeps related sentences together (e.g. the whole 'Token Generation'
    section) while dropping unrelated parts of the same file.

    A '###' subsection also gets its parent '##' heading copied on top,
    so it keeps its context. E.g. '### users' becomes:
        ## Tables
        ### users
    Without that, nothing in the chunk would say it's about a table.
    """
    sections = []
    current_lines = []
    parent_heading = None
    for line in text.splitlines():
        if line.startswith("#"):
            # A heading ends the previous chunk and starts a new one
            if current_lines:
                sections.append("\n".join(current_lines).strip())
            current_lines = []
            if line.startswith("## "):
                parent_heading = line
            elif line.startswith("### ") and parent_heading:
                current_lines.append(parent_heading)
        current_lines.append(line)
    if current_lines:
        sections.append("\n".join(current_lines).strip())
    return [s for s in sections if s]  # skip empty chunks


def count_matched_words(query, text):
    """How many DIFFERENT meaningful query words appear in the text."""
    return len(query_words(query) & set(tokenize(text)))


# Guardrail setting: the fraction of the question's meaningful words a
# snippet must contain before we treat it as evidence.
# 0.5 = at least half. Below that, the match is probably a coincidence.
MIN_QUERY_COVERAGE = 0.5


class DocuBot:
    def __init__(self, docs_folder="docs", llm_client=None):
        """
        docs_folder: directory containing project documentation files
        llm_client: optional Gemini client for LLM based answers
        """
        self.docs_folder = docs_folder
        self.llm_client = llm_client

        # Load documents into memory
        self.documents = self.load_documents()  # List of (filename, text)

        # Split each document into smaller sections (chunks).
        # List of (filename, section_text)
        self.chunks = self.chunk_documents(self.documents)

        # Build a retrieval index over the chunks (implemented in Phase 1)
        self.index = self.build_index(self.chunks)

    # -----------------------------------------------------------
    # Document Loading
    # -----------------------------------------------------------

    def load_documents(self):
        """
        Loads all .md and .txt files inside docs_folder.
        Returns a list of tuples: (filename, text)
        """
        docs = []
        pattern = os.path.join(self.docs_folder, "*.*")
        for path in glob.glob(pattern):
            if path.endswith(".md") or path.endswith(".txt"):
                with open(path, "r", encoding="utf8") as f:
                    text = f.read()
                filename = os.path.basename(path)
                docs.append((filename, text))
        return docs

    # -----------------------------------------------------------
    # Index Construction (Phase 1)
    # -----------------------------------------------------------

    def chunk_documents(self, documents):
        """
        Splits every document into sections (see split_into_sections).
        Returns a list of (filename, section_text). The filename is kept
        so we can always show where a snippet came from.
        """
        chunks = []
        for filename, text in documents:
            for section in split_into_sections(text):
                chunks.append((filename, section))
        return chunks

    def build_index(self, chunks):
        """
        Build a tiny inverted index mapping lowercase words to the chunks
        they appear in. Chunks are identified by their position in
        self.chunks (0, 1, 2, ...), since one file now has many chunks.

        Example structure:
        {
            "token": [3, 4, 9, 15],
            "database": [20, 21, 30]
        }
        """
        index = {}
        for chunk_id, (filename, text) in enumerate(chunks):
            # set() so each word is recorded once per chunk, even if it repeats
            for word in set(tokenize(text)):
                if word not in index:
                    index[word] = []
                index[word].append(chunk_id)
        return index

    # -----------------------------------------------------------
    # Scoring and Retrieval (Phase 1)
    # -----------------------------------------------------------

    def score_document(self, query, text):
        """
        Return a simple relevance score for how well the text matches the query:
        the total number of times the question's meaningful words appear.
        A snippet that mentions "token" 15 times beats one that mentions it once.
        """
        words = query_words(query)

        # Count every word in the snippet once, up front
        word_counts = {}
        for word in tokenize(text):
            word_counts[word] = word_counts.get(word, 0) + 1

        score = 0
        for word in words:
            score += word_counts.get(word, 0)
        return score

    def retrieve(self, query, top_k=3):
        """
        Select the top_k most relevant snippets (chunks) for the query.
        Return a list of (filename, text), best first.
        """
        # Step 1: use the index to find candidate chunks that contain
        # at least one meaningful query word (skip chunks with no overlap)
        candidates = set()
        for word in query_words(query):
            for chunk_id in self.index.get(word, []):
                candidates.add(chunk_id)

        # Step 2: keep only chunks that pass the guardrail, and score them
        ranked = []
        for chunk_id in candidates:
            filename, text = self.chunks[chunk_id]
            if not self.has_enough_evidence(query, text):
                continue  # guardrail: too few query words, likely coincidence
            matched = count_matched_words(query, text)
            score = self.score_document(query, text)
            ranked.append((matched, score, filename, text))

        # Step 3: sort best first.
        # First by how many DIFFERENT question words the chunk contains,
        # then (to break ties) by the total count score.
        # This stops a chunk that repeats one word many times from beating
        # a chunk that matches the whole question.
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)

        # Return (filename, text) pairs, as the rest of the code expects.
        # If nothing passed the guardrail this is [], and both answer
        # modes reply "I do not know based on these docs."
        results = [(filename, text) for matched, score, filename, text in ranked]
        return results[:top_k]

    def has_enough_evidence(self, query, text):
        """
        Guardrail: does this snippet contain enough of the question
        to count as evidence?

        Rule: at least MIN_QUERY_COVERAGE (half) of the meaningful query
        words must appear in the snippet. One stray shared word
        (like 'docs') is not enough.
        """
        total = len(query_words(query))
        if total == 0:
            return False  # question had only stopwords; nothing to match on
        coverage = count_matched_words(query, text) / total
        return coverage >= MIN_QUERY_COVERAGE

    # -----------------------------------------------------------
    # Answering Modes
    # -----------------------------------------------------------

    def answer_retrieval_only(self, query, top_k=3):
        """
        Phase 1 retrieval only mode.
        Returns raw snippets and filenames with no LLM involved.
        """
        snippets = self.retrieve(query, top_k=top_k)

        if not snippets:
            return "I do not know based on these docs."

        formatted = []
        for filename, text in snippets:
            formatted.append(f"[{filename}]\n{text}\n")

        return "\n---\n".join(formatted)

    def answer_rag(self, query, top_k=3):
        """
        Phase 2 RAG mode.
        Uses student retrieval to select snippets, then asks Gemini
        to generate an answer using only those snippets.
        """
        if self.llm_client is None:
            raise RuntimeError(
                "RAG mode requires an LLM client. Provide a GeminiClient instance."
            )

        snippets = self.retrieve(query, top_k=top_k)

        if not snippets:
            return "I do not know based on these docs."

        return self.llm_client.answer_from_snippets(query, snippets)

    # -----------------------------------------------------------
    # Bonus Helper: concatenated docs for naive generation mode
    # -----------------------------------------------------------

    def full_corpus_text(self):
        """
        Returns all documents concatenated into a single string.
        This is used in Phase 0 for naive 'generation only' baselines.
        """
        return "\n\n".join(text for _, text in self.documents)
