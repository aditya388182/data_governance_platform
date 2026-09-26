(.venv) ubuntu@:~/data_governance_platform$ docker image inspect cgr.dev/chainguard/minio:latest -f '{{index .RepoDigests 0}}'
docker image inspect cgr.dev/chainguard/minio-client:latest -f '{{index .RepoDigests 0}}'
cgr.dev/chainguard/minio@sha256:bd014394a80898e68c149f2311fdf8d5a2c2f3bb2c33b9327ae6d02b4b065ae1
cgr.dev/chainguard/minio-client@sha256:b8b144ab34694ecea25aa352c4be9de4c26ee2a02701521dce02ee5593c57338
