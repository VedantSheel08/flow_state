"""Collision observations are not crash counts. Keep both explicit."""
from collections import Counter


class SafetyObservations:
    def __init__(self):
        self.records = 0
        self.episodes = 0
        self.by_type = Counter()
        self.pairs = set()
        self.previous = set()
        self.examples = []

    def observe(self, t, collisions):
        current = set()
        for collision in collisions:
            pair = tuple(sorted((collision.collider, collision.victim)))
            key = (collision.type, pair)
            current.add(key)
            self.records += 1
            self.by_type[collision.type] += 1
            self.pairs.add(pair)
            if key not in self.previous:
                self.episodes += 1
                if len(self.examples) < 50:
                    self.examples.append({"t": t, "type": collision.type, "collider": collision.collider,
                                          "victim": collision.victim, "lane": collision.lane,
                                          "colliderType": collision.colliderType, "victimType": collision.victimType,
                                          "colliderSpeed": collision.colliderSpeed, "victimSpeed": collision.victimSpeed})
        self.previous = current

    def summary(self):
        return {"junctionChecks": True, "collisionRecordObservations": self.records,
                "collisionEpisodes": self.episodes, "uniqueCollisionPairs": len(self.pairs),
                "recordObservationsByType": dict(self.by_type), "examples": self.examples,
                "definition": "An episode starts when a pair/type is reported after an unreported step; not a count of unique physical crashes."}
