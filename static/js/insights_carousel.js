document.addEventListener('DOMContentLoaded', function() {
    const carouselContainer = document.querySelector('.insights-carousel-container');
    if (!carouselContainer) return;

    const carousel = carouselContainer.querySelector('.insights-carousel');
    const slides = carousel.querySelectorAll('.insight-slide');
    const dots = carouselContainer.querySelectorAll('.indicator-dot');
    
    if (slides.length <= 1) {
        if (dots.length) dots[0].parentElement.style.display = 'none';
        return;
    }

    let currentIndex = 0;
    let autoPlayInterval;

    function updateActiveDot(index) {
        dots.forEach((dot, i) => {
            dot.classList.toggle('active', i === index);
        });
    }

    function goToSlide(index) {
        if (index < 0) index = slides.length - 1;
        if (index >= slides.length) index = 0;
        
        currentIndex = index;
        const slideWidth = carousel.offsetWidth;
        carousel.scrollTo({
            left: slideWidth * index,
            behavior: 'smooth'
        });
        updateActiveDot(index);
    }

    // Auto play logic
    function startAutoPlay() {
        stopAutoPlay();
        autoPlayInterval = setInterval(() => {
            goToSlide(currentIndex + 1);
        }, 5000);
    }

    function stopAutoPlay() {
        if (autoPlayInterval) {
            clearInterval(autoPlayInterval);
        }
    }

    // Manual navigation via dots
    dots.forEach((dot, index) => {
        dot.addEventListener('click', () => {
            goToSlide(index);
            stopAutoPlay();
            // Optional: restart autoplay after delay
            setTimeout(startAutoPlay, 2000);
        });
    });

    // Detect manual scroll/swipe to update dots
    let scrollTimeout;
    carousel.addEventListener('scroll', () => {
        clearTimeout(scrollTimeout);
        scrollTimeout = setTimeout(() => {
            const slideWidth = carousel.offsetWidth;
            const newIndex = Math.round(carousel.scrollLeft / slideWidth);
            if (newIndex !== currentIndex) {
                currentIndex = newIndex;
                updateActiveDot(newIndex);
            }
        }, 100);
    });

    // Interaction handling
    carousel.addEventListener('mouseenter', stopAutoPlay);
    carousel.addEventListener('mouseleave', startAutoPlay);
    
    // Support touch to pause
    carousel.addEventListener('touchstart', stopAutoPlay);
    carousel.addEventListener('touchend', () => {
        setTimeout(startAutoPlay, 2000);
    });

    // Initial start
    startAutoPlay();

    // Handle window resize
    window.addEventListener('resize', () => {
        // Re-align to current slide on resize
        goToSlide(currentIndex);
    });
});
