default:
    @just --list

update-deps:
    @echo "🚧 Updating deps..."
    python scripts/update_dependencies.py
    @echo "✅ Done!"

release version type number:
    #!/usr/bin/env bash
    if [ "{{type}}" = "stable" ]; then
        TAG="v{{version}}"
    else
        TAG="v{{version}}-beta.{{number}}"
    fi
    echo "🚀 Releasing... $TAG"
    git tag -a "$TAG" -m "Bugster CLI $TAG" && git push origin --tags
    echo "✅ Done!"
