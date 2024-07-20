# infer-kernel-config
Infer a linux kernel configuration using kallsyms information


Running:

```
docker build . -t infer-kernel-config
docker run -v $(pwd):/root -it infer-kernel-config /bin/bash
```
