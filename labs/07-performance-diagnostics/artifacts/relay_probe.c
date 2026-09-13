#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static unsigned long relay_checksum(unsigned long count)
{
    unsigned long checksum = 0;
    unsigned long index;

    for (index = 0; index < count; index++)
        checksum = (checksum * 33UL + index) % 1000003UL;
    return checksum;
}

int main(int argc, char **argv)
{
    unsigned long count = 2000000UL;

    if (argc == 2 && strcmp(argv[1], "--crash") == 0)
        raise(SIGSEGV);
    if (argc == 2)
        count = strtoul(argv[1], NULL, 10);
    printf("pid=%ld checksum=%lu\n", (long)getpid(), relay_checksum(count));
    return 0;
}
