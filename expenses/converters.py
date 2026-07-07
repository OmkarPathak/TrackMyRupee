import uuid

class UUIDOrIntConverter:
    regex = '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[0-9a-fA-F]{32}|[0-9]+'

    def to_python(self, value):
        try:
            return uuid.UUID(value)
        except ValueError:
            return int(value)

    def to_url(self, value):
        return str(value)
