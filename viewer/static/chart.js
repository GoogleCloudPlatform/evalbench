function drawChart() {
    const data = window.chartData;
    const xCol = window.chartConfig.xCol;
    const yCol = window.chartConfig.yCol;
    const hueCol = window.chartConfig.hueCol;
    const title = window.chartConfig.title;
    const ylabel = window.chartConfig.ylabel;
    
    const margin = {top: 60, right: 180, bottom: 60, left: 60};
    const container = document.getElementById('chart-container');
    if (!container) return;
    
    const width = container.clientWidth - margin.left - margin.right;
    const height = 500 - margin.top - margin.bottom;
    
    // Clear previous SVG
    d3.select("#chart").selectAll("*").remove();
    
    const svg = d3.select("#chart")
        .append("svg")
        .attr("width", width + margin.left + margin.right)
        .attr("height", height + margin.top + margin.bottom)
        .append("g")
        .attr("transform", `translate(${margin.left},${margin.top})`);
        
    // X axis
    const x = d3.scalePoint()
        .domain(data.map(d => d[xCol]))
        .range([0, width]);
        
    // Y axis
    const y = d3.scaleLinear()
        .domain([0, d3.max(data, d => d[yCol]) * 1.1])
        .range([height, 0]);
        
    // Grid lines
    svg.append("g")
        .attr("class", "grid")
        .attr("transform", `translate(0,${height})`)
        .call(d3.axisBottom(x).tickSize(-height).tickFormat(""));

    svg.append("g")
        .attr("class", "grid")
        .call(d3.axisLeft(y).tickSize(-width).tickFormat(""));
        
    // Axes
    svg.append("g")
        .attr("transform", `translate(0,${height})`)
        .call(d3.axisBottom(x))
        .selectAll("text")
        .attr("transform", "rotate(-45)")
        .style("text-anchor", "end")
        .attr("class", "axis-label");
        
    svg.append("g")
        .call(d3.axisLeft(y))
        .selectAll("text")
        .attr("class", "axis-label");
        
    // Color scale - Premium palette
    const products = [...new Set(data.map(d => d[hueCol]))];
    const colors = ['#6366f1', '#10b981', '#f43f5e', '#f59e0b', '#8b5cf6'];
    const color = d3.scaleOrdinal()
        .domain(products)
        .range(colors);
        
    // Group data by product
    const dataByProduct = d3.group(data, d => d[hueCol]);
    
    // Draw smooth lines and areas
    dataByProduct.forEach((productData, product) => {
        // Area
        svg.append("path")
            .datum(productData)
            .attr("class", "area")
            .attr("d", d3.area()
                .x(d => x(d[xCol]))
                .y0(height)
                .y1(d => y(d[yCol]))
                .curve(d3.curveCardinal)
            )
            .style("fill", color(product));

        // Line
        svg.append("path")
            .datum(productData)
            .attr("class", "line")
            .attr("d", d3.line()
                .x(d => x(d[xCol]))
                .y(d => y(d[yCol]))
                .curve(d3.curveCardinal)
            )
            .style("stroke", color(product));
    });
    
    // Add dots and tooltips
    const tooltip = d3.select("#tooltip");
    
    data.forEach(d => {
        svg.append("circle")
            .attr("cx", x(d[xCol]))
            .attr("cy", y(d[yCol]))
            .attr("r", 5)
            .attr("fill", color(d[hueCol]))
            .attr("class", "dot")
            .on("mouseover", function(event) {
                d3.select(this).attr("r", 8).style("stroke-width", "3px");
                tooltip.style("opacity", 1)
                    .html(`<strong>Product:</strong> ${d[hueCol]}<br/><strong>Time:</strong> ${d[xCol]}<br/><strong>Value:</strong> ${d[yCol]}`);
            })
            .on("mousemove", function(event) {
                tooltip.style("left", (event.pageX + 15) + "px")
                       .style("top", (event.pageY - 28) + "px");
            })
            .on("mouseout", function() {
                d3.select(this).attr("r", 5).style("stroke-width", "2px");
                tooltip.style("opacity", 0);
            });
    });
    
    // Add Title
    svg.append("text")
        .attr("x", width / 2)
        .attr("y", -margin.top / 2)
        .attr("text-anchor", "middle")
        .attr("class", "chart-title")
        .text(title);
        
    // Add Y axis label
    svg.append("text")
        .attr("transform", "rotate(-90)")
        .attr("y", -margin.left + 20)
        .attr("x", -height / 2)
        .attr("text-anchor", "middle")
        .style("font-size", "12px")
        .style("fill", "#64748b")
        .style("font-weight", "600")
        .text(ylabel);
        
    // Add Legend
    const legend = svg.selectAll(".legend")
        .data(products)
        .enter().append("g")
        .attr("class", "legend")
        .attr("transform", (d, i) => `translate(${width + 20}, ${i * 25})`);
        
    legend.append("rect")
        .attr("x", 0)
        .attr("width", 12)
        .attr("height", 12)
        .attr("rx", 3)
        .style("fill", color);
        
    legend.append("text")
        .attr("x", 20)
        .attr("y", 6)
        .attr("dy", ".35em")
        .style("text-anchor", "start")
        .text(d => d);
}

// Initial draw
drawChart();

// Redraw on resize
window.addEventListener('resize', drawChart);
