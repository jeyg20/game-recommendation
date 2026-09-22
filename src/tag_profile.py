"""Build a taste profile from the owned library, weighted against a Steam-wide tag corpus."""

# ---------------------------------------------------------------------
# 1. Background corpus (one-time, cached)
# ---------------------------------------------------------------------
# TODO: collect appids from `all` pages 0, 2, 5 (1 req/60s) + top100forever + top100in2weeks
# TODO: dedupe — the top100s are mostly already inside `all` page 0
# TODO: random-sample ~1000 appids from that pool (appdetails is 1 req/sec, don't fetch all 3000)
# TODO: appdetails each -> cache raw {appid: tags} to disk; this is the expensive step, never refetch
# TODO: drop untagged games and Utilities genre — same filters as candidates get
# TODO: sanity check: most common corpus tags should be Indie/Action/Singleplayer/Casual.
#       If Open World Survival Craft is near the top, the sample is contaminated.

# ---------------------------------------------------------------------
# 2. df table
# ---------------------------------------------------------------------
# TODO: df = how many corpus games carry each tag
# TODO: store N alongside df in the same file — IDF is wrong if they ever drift apart
# TODO: N counts tagged games only
# TODO: idf = log((1 + N) / (1 + df)) + 1   (smoothed, never zero)

# ---------------------------------------------------------------------
# 3. Taste weight per owned game
# ---------------------------------------------------------------------
# TODO: completion = min(playtime_hours / main_story_hours, 1)   capped, no blowout
# TODO: volume = 1 - exp(-playtime_hours / 40)                   saturating
# TODO: weight = mean of the two
# TODO: no main_story, or multiplayer/idle (HLTB denominator is junk there) -> volume only
# TODO: later — negative weight for early abandonment instead of a positive floor

# ---------------------------------------------------------------------
# 4. Profile vector
# ---------------------------------------------------------------------
# TODO: per game, normalize tag votes to proportions (share of that game's total votes)
# TODO: profile[tag] += weight * share * idf
# TODO: keep the whole dict — do NOT truncate to top-N, that dict is the vector
# TODO: top-N is only for pulling the candidate pool, not for scoring

# ---------------------------------------------------------------------
# 5. Later — ranking
# ---------------------------------------------------------------------
# TODO: cosine(a, b) = dot / (|a| * |b|)
# TODO: cache the profile's norm, it doesn't change across candidates
# TODO: candidate vectors must use the SAME df table, or the axes don't line up

# ---------------------------------------------------------------------
# Blocker
# ---------------------------------------------------------------------
# TODO: normalize appid int/str at the cache boundary before any of this —
#       subtracting owned appids from candidates fails silently otherwise
