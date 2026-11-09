# Worker container syscall profile

`seccomp.json` is the unmodified Moby v28.1.1 default profile from
https://github.com/moby/moby/blob/v28.1.1/profiles/seccomp/default.json
licensed under the accompanying Apache 2.0 license.

On the verified Docker host, even an explicit OCI Landlock allow rule returns
ENOSYS; the host syscall resolver is too old. The Worker container therefore
starts with `seccomp=unconfined`, **drops all capabilities**, and uses
no-new-privileges. Before opening its queue, Worker installs this default-deny
profile using the newer libseccomp inside its image. The filter is inherited by
all children across exec. Capability-gated and ptrace compatibility allow rules
are omitted; architecture and argument filters are retained. A load failure
terminates Worker. Query children then add Landlock and a stricter network/exec
filter. This bootstrap arrangement is not an unrestricted query fallback.

The default Docker profile still applies to API, PostgreSQL and maintenance.
