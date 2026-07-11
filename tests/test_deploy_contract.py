from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_publish_workflow_uses_sha_tag_and_provenance() -> None:
    workflow = (ROOT / ".github/workflows/publish-image.yml").read_text()

    assert "ghcr.io/${{ github.repository }}" in workflow
    assert "type=raw,value=${{ github.sha }}" in workflow
    assert "org.opencontainers.image.revision=${{ github.sha }}" in workflow
    assert "actions/attest@" in workflow


def test_deploy_pulls_and_verifies_immutable_image() -> None:
    deploy = (ROOT / "ops/deploy.sh").read_text()

    assert "ghcr.io/surzhikxv/ib-services" in deploy
    assert 'docker pull "$requested_image"' in deploy
    assert "org.opencontainers.image.revision" in deploy
    assert "RepoDigests" in deploy
    assert "KONTUR_ALLOW_LOCAL_BUILD" in deploy


def test_compose_does_not_implicitly_build_production_image() -> None:
    compose = (ROOT / "docker-compose.yml").read_text()

    assert "build: ." not in compose
