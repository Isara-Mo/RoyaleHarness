#pragma once

// Full producer chains are an unfinished experiment, not part of the default
// probe. Keep the initializer/lifecycle hooks out of stable candidate builds.
#ifndef CR_EXPERIMENTAL_PRODUCER_ORIGINS
#define CR_EXPERIMENTAL_PRODUCER_ORIGINS 0
#endif

#if CR_EXPERIMENTAL_PRODUCER_ORIGINS != 0 && CR_EXPERIMENTAL_PRODUCER_ORIGINS != 1
#error CR_EXPERIMENTAL_PRODUCER_ORIGINS must be 0 or 1
#endif
